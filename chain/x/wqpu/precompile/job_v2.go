package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func JobCompleted(state WordState, jobID common.Hash) (bool, error) {
	value, err := GetUint64(state, "completed-job", jobID.Bytes())
	if err != nil { return false, err }
	return value != 0, nil
}

func MarkJobCompleted(state WordState, jobID common.Hash) error {
	if jobID == (common.Hash{}) { return errors.New("WQPU job id is required") }
	completed, err := JobCompleted(state, jobID)
	if err != nil { return err }
	if completed { return errors.New("WQPU job is already completed") }
	return SetUint64(state, "completed-job", jobID.Bytes(), 1)
}

func CommitJobReservationV2(state WordState, request JobRequest, requesterSession common.Address, height uint64) (JobReservation, error) {
	completed, err := JobCompleted(state, request.JobID)
	if err != nil { return JobReservation{}, err }
	if completed { return JobReservation{}, errors.New("completed WQPU job id cannot be reused") }
	if height > ^uint64(0)-JobTTLBlocks { return JobReservation{}, errors.New("WQPU job expiry overflow") }
	expires := height + JobTTLBlocks
	requester, err := ActiveSessionForPermission(state, request.RequesterWallet, requesterSession, height, SessionPermJob)
	if err != nil { return JobReservation{}, err }
	if requester.Delegation.ExpiresHeight < expires {
		return JobReservation{}, errors.New("requester WQPU session expires before job")
	}
	for _, reservation := range request.Providers {
		provider, exists, err := LoadPeerProvider(state, reservation.ProviderPeerID)
		if err != nil { return JobReservation{}, err }
		if !exists || provider.Wallet != reservation.ProviderWallet {
			return JobReservation{}, errors.New("WQPU job references unknown provider")
		}
		control, exists, err := LoadPeerControlSession(state, reservation.ProviderPeerID)
		if err != nil { return JobReservation{}, err }
		if !exists { return JobReservation{}, errors.New("WQPU provider has no control session") }
		providerSession, err := ActiveSessionForPermission(state, provider.Wallet, control, height, SessionPermProvider)
		if err != nil { return JobReservation{}, err }
		if providerSession.Delegation.ExpiresHeight < expires {
			return JobReservation{}, errors.New("provider WQPU session expires before job")
		}
	}
	return CommitJobReservation(state, request, requesterSession, height)
}

func CommitSignedJobReservationV2(state WordState, envelope JobReserveEnvelope, config NetworkConfig, height uint64) (JobReservation, error) {
	action, err := VerifyJobReserve(state, envelope, config, height)
	if err != nil { return JobReservation{}, err }
	job, err := CommitJobReservationV2(state, envelope.Request, envelope.Session, height)
	if err != nil { return JobReservation{}, err }
	if err := AdvanceSessionActionNonce(state, envelope.Wallet, envelope.Session, action.ActionNonce); err != nil { return JobReservation{}, err }
	return job, nil
}
