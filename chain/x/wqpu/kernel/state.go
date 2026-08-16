package kernel

import (
	"encoding/hex"
	"errors"
)

type ProviderReservation struct {
	ProviderWallet       string
	ProviderPeerID       string
	ReservedComputeUnits uint64
	AssignedModelBytes   uint64
}

type JobReservation struct {
	JobID                  string
	RequesterWallet        string
	RequesterSessionPubkey [32]byte
	ModelHash               string
	PriceEpoch              uint64
	PricePerMillionUnits    uint64
	MaxComputeUnits         uint64
	MaxChargeUnits          uint64
	CreatedHeight           uint64
	ExpiresHeight           uint64
	Providers               []ProviderReservation
}

func (j JobReservation) Validate() error {
	if j.JobID == "" || j.RequesterWallet == "" || j.ModelHash == "" {
		return errors.New("job identity fields must be non-empty")
	}
	var zero [32]byte
	if j.RequesterSessionPubkey == zero {
		return errors.New("job session key must be non-zero")
	}
	if j.PricePerMillionUnits == 0 || j.MaxComputeUnits == 0 || j.MaxChargeUnits == 0 {
		return errors.New("job price, compute and charge limits must be positive")
	}
	if j.ExpiresHeight <= j.CreatedHeight {
		return errors.New("job expiry must follow creation")
	}
	if len(j.Providers) == 0 {
		return errors.New("job must reserve at least one provider")
	}
	seen := map[string]struct{}{}
	for _, p := range j.Providers {
		if p.ProviderWallet == "" || p.ProviderPeerID == "" {
			return errors.New("provider reservation identity must be non-empty")
		}
		if p.ReservedComputeUnits == 0 || p.AssignedModelBytes == 0 {
			return errors.New("provider reservation must contain compute and model bytes")
		}
		if _, ok := seen[p.ProviderPeerID]; ok {
			return errors.New("duplicate provider in job")
		}
		seen[p.ProviderPeerID] = struct{}{}
	}
	return nil
}

type State struct {
	ChainID               string
	Height                uint64
	Epoch                 uint64
	PricePerMillionUnits  uint64
	Sessions              map[string]*SessionState
	WalletRevocationNonce map[string]uint64
	Providers             map[string]Provider
	PeerOwners            map[string]string
	Jobs                  map[string]JobReservation
	ReservedByPeer        map[string]uint64
}

func NewState(chainID string, initialPrice uint64) (*State, error) {
	if chainID == "" || initialPrice == 0 {
		return nil, errors.New("chain id and initial price are required")
	}
	return &State{
		ChainID:               chainID,
		PricePerMillionUnits:  initialPrice,
		Sessions:              map[string]*SessionState{},
		WalletRevocationNonce: map[string]uint64{},
		Providers:             map[string]Provider{},
		PeerOwners:            map[string]string{},
		Jobs:                  map[string]JobReservation{},
		ReservedByPeer:        map[string]uint64{},
	}, nil
}

func sessionKey(wallet string, pubkey [32]byte) string {
	return wallet + ":" + hex.EncodeToString(pubkey[:])
}

func (s *State) session(wallet string, pubkey [32]byte) (*SessionState, error) {
	if s == nil {
		return nil, errors.New("nil state")
	}
	session := s.Sessions[sessionKey(wallet, pubkey)]
	if session == nil {
		return nil, errors.New("unknown session")
	}
	return session, nil
}

// AuthorizeSession applies a wallet-signature-verified delegation.
func (s *State) AuthorizeSession(d SessionDelegation) error {
	if s == nil {
		return errors.New("nil state")
	}
	if err := d.Validate(); err != nil {
		return err
	}
	if d.ChainID != s.ChainID {
		return errors.New("session belongs to another chain")
	}
	if d.IssuedHeight > s.Height || d.ExpiresHeight <= s.Height {
		return errors.New("session is not valid at current height")
	}
	if d.RevocationNonce != s.WalletRevocationNonce[d.Wallet] {
		return errors.New("stale or future wallet revocation nonce")
	}
	key := sessionKey(d.Wallet, d.SessionPubkey)
	if _, exists := s.Sessions[key]; exists {
		return errors.New("session already authorized")
	}
	s.Sessions[key] = &SessionState{Delegation: d}
	return nil
}

// RevokeWalletSessions applies a wallet-signature-verified nonce increase.
func (s *State) RevokeWalletSessions(wallet string, newNonce uint64) error {
	if s == nil || wallet == "" {
		return errors.New("wallet is required")
	}
	current := s.WalletRevocationNonce[wallet]
	if newNonce <= current {
		return errors.New("revocation nonce must increase")
	}
	s.WalletRevocationNonce[wallet] = newNonce
	for _, session := range s.Sessions {
		if session.Delegation.Wallet == wallet && session.Delegation.RevocationNonce < newNonce {
			session.Revoke()
		}
	}
	return nil
}

// PublishProvider applies a session-signature-verified provider heartbeat.
func (s *State) PublishProvider(wallet string, sessionPubkey [32]byte, p Provider) error {
	session, err := s.session(wallet, sessionPubkey)
	if err != nil {
		return err
	}
	if err := session.CanAuthorize(s.Height, SessionPermProvider, 0); err != nil {
		return err
	}
	if p.Wallet != wallet {
		return errors.New("provider wallet does not match authorized session")
	}
	if p.HeartbeatHeight != s.Height {
		return errors.New("provider heartbeat must use current height")
	}
	if err := p.Validate(s.Height); err != nil {
		return err
	}
	if owner, exists := s.PeerOwners[p.PeerID]; exists && owner != wallet {
		return errors.New("peer id already belongs to another wallet")
	}
	if old, exists := s.Providers[wallet]; exists {
		if p.HeartbeatHeight <= old.HeartbeatHeight {
			return errors.New("provider heartbeat must strictly increase")
		}
		if p.PeerID != old.PeerID {
			if s.ReservedByPeer[old.PeerID] != 0 {
				return errors.New("cannot change peer id while work is reserved")
			}
			delete(s.PeerOwners, old.PeerID)
		}
	}
	s.Providers[wallet] = p
	s.PeerOwners[p.PeerID] = wallet
	return nil
}

func (s *State) activeProviderByPeer(peerID string) (Provider, bool) {
	wallet, ok := s.PeerOwners[peerID]
	if !ok {
		return Provider{}, false
	}
	p, ok := s.Providers[wallet]
	if !ok || p.PeerID != peerID || p.Validate(s.Height) != nil {
		return Provider{}, false
	}
	return p, true
}

// ReserveJob applies a verified session transaction atomically.
func (s *State) ReserveJob(j JobReservation) error {
	if s == nil {
		return errors.New("nil state")
	}
	if err := j.Validate(); err != nil {
		return err
	}
	if _, exists := s.Jobs[j.JobID]; exists {
		return errors.New("job id already exists")
	}
	if j.CreatedHeight != s.Height {
		return errors.New("job must be created at current height")
	}
	if j.PriceEpoch != s.Epoch || j.PricePerMillionUnits != s.PricePerMillionUnits {
		return errors.New("job uses stale global price")
	}

	session, err := s.session(j.RequesterWallet, j.RequesterSessionPubkey)
	if err != nil {
		return err
	}
	if err := session.CanAuthorize(s.Height, SessionPermJob, j.MaxChargeUnits); err != nil {
		return err
	}

	for _, reservation := range j.Providers {
		provider, ok := s.activeProviderByPeer(reservation.ProviderPeerID)
		if !ok {
			return errors.New("job selected unknown or expired provider")
		}
		if provider.Wallet != reservation.ProviderWallet {
			return errors.New("provider wallet/peer mismatch")
		}
		if !containsModel(provider.ModelHashes, j.ModelHash) {
			return errors.New("provider does not advertise requested model")
		}
		if j.ExpiresHeight > provider.ExpiresHeight {
			return errors.New("provider heartbeat expires before job")
		}
		current := s.ReservedByPeer[provider.PeerID]
		if current > provider.CapacityUnits || reservation.ReservedComputeUnits > provider.CapacityUnits-current {
			return errors.New("provider capacity already reserved")
		}
	}

	if err := session.Reserve(s.Height, SessionPermJob, j.MaxChargeUnits); err != nil {
		return err
	}
	for _, reservation := range j.Providers {
		s.ReservedByPeer[reservation.ProviderPeerID] += reservation.ReservedComputeUnits
	}
	s.Jobs[j.JobID] = j
	return nil
}

func (s *State) canReleaseJobResources(j JobReservation) error {
	for _, reservation := range j.Providers {
		if reservation.ReservedComputeUnits > s.ReservedByPeer[reservation.ProviderPeerID] {
			return errors.New("corrupt provider reservation accounting")
		}
	}
	return nil
}

func (s *State) releaseJobResources(j JobReservation) {
	for _, reservation := range j.Providers {
		current := s.ReservedByPeer[reservation.ProviderPeerID]
		remaining := current - reservation.ReservedComputeUnits
		if remaining == 0 {
			delete(s.ReservedByPeer, reservation.ProviderPeerID)
		} else {
			s.ReservedByPeer[reservation.ProviderPeerID] = remaining
		}
	}
}

// CloseJob is called only after receipt verification determines the actual charge.
func (s *State) CloseJob(jobID string, actualCharge uint64) error {
	j, ok := s.Jobs[jobID]
	if !ok {
		return errors.New("unknown job")
	}
	session, err := s.session(j.RequesterWallet, j.RequesterSessionPubkey)
	if err != nil {
		return err
	}
	if err := session.CanSettle(j.MaxChargeUnits, actualCharge); err != nil {
		return err
	}
	if err := s.canReleaseJobResources(j); err != nil {
		return err
	}

	// All fallible checks are complete; commit both accounting changes together.
	s.releaseJobResources(j)
	if err := session.Settle(j.MaxChargeUnits, actualCharge); err != nil {
		panic(err) // invariant violation after successful preflight
	}
	delete(s.Jobs, jobID)
	return nil
}

func (s *State) preflightExpiry(targetHeight uint64) error {
	for _, j := range s.Jobs {
		if j.ExpiresHeight > targetHeight {
			continue
		}
		session, err := s.session(j.RequesterWallet, j.RequesterSessionPubkey)
		if err != nil {
			return err
		}
		if err := session.CanRelease(j.MaxChargeUnits); err != nil {
			return err
		}
		if err := s.canReleaseJobResources(j); err != nil {
			return err
		}
	}
	return nil
}

func (s *State) expireJobs() {
	for id, j := range s.Jobs {
		if j.ExpiresHeight > s.Height {
			continue
		}
		session, _ := s.session(j.RequesterWallet, j.RequesterSessionPubkey)
		s.releaseJobResources(j)
		if err := session.Release(j.MaxChargeUnits); err != nil {
			panic(err) // preflight established this invariant
		}
		delete(s.Jobs, id)
	}
}

func (s *State) expireProviders() {
	for wallet, p := range s.Providers {
		if p.ExpiresHeight > s.Height {
			continue
		}
		// A valid reservation cannot outlive its provider heartbeat. If this is
		// non-zero, state invariants were violated earlier, so keep the record.
		if s.ReservedByPeer[p.PeerID] != 0 {
			continue
		}
		delete(s.PeerOwners, p.PeerID)
		delete(s.Providers, wallet)
	}
}

func (s *State) AdvanceHeight(blocks uint64) error {
	if s == nil || blocks == 0 {
		return errors.New("positive block count required")
	}
	if blocks > ^uint64(0)-s.Height {
		return errors.New("height overflow")
	}
	target := s.Height + blocks
	if err := s.preflightExpiry(target); err != nil {
		return err
	}
	s.Height = target
	s.expireJobs()
	s.expireProviders()
	return nil
}

func (s *State) ActiveCapacityUnits() uint64 {
	var total uint64
	for _, p := range s.Providers {
		if p.Validate(s.Height) != nil {
			continue
		}
		if p.CapacityUnits > ^uint64(0)-total {
			return ^uint64(0)
		}
		total += p.CapacityUnits
	}
	return total
}

func (s *State) ReservedDemandUnits() uint64 {
	var total uint64
	for peerID, units := range s.ReservedByPeer {
		if _, ok := s.activeProviderByPeer(peerID); !ok {
			continue
		}
		if units > ^uint64(0)-total {
			return ^uint64(0)
		}
		total += units
	}
	return total
}

func (s *State) ClosePriceEpoch() (GlobalPriceState, error) {
	if s == nil {
		return GlobalPriceState{}, errors.New("nil state")
	}
	next, err := AggregatePriceState(
		s.ActiveCapacityUnits(),
		s.ReservedDemandUnits(),
		s.PricePerMillionUnits,
		s.Epoch+1,
	)
	if err != nil {
		return GlobalPriceState{}, err
	}
	s.Epoch = next.Epoch
	s.PricePerMillionUnits = next.PricePerMillionUnits
	return next, nil
}
