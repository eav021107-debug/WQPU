package kernel

import "errors"

// AddBondedPriceCapacity mirrors the native precompile's economically backed
// provider capacity. The transaction boundary is responsible for locking the
// corresponding native WQPU before calling this reference transition.
func (s *State) AddBondedPriceCapacity(peerID string, additional uint64) error {
	if s == nil || peerID == "" || additional == 0 {
		return errors.New("valid peer and positive bonded capacity required")
	}
	provider, ok := s.activeProviderByPeer(peerID)
	if !ok {
		return errors.New("cannot bond unknown or inactive provider")
	}
	current := s.BondedCapacityByPeer[peerID]
	if additional > ^uint64(0)-current {
		return errors.New("bonded provider capacity overflow")
	}
	updated := current + additional
	if updated > provider.CapacityUnits {
		return errors.New("bonded capacity exceeds provider advertisement")
	}
	s.BondedCapacityByPeer[peerID] = updated
	return nil
}

func (s *State) RemoveBondedPriceCapacity(peerID string, amount uint64) error {
	if s == nil || peerID == "" || amount == 0 {
		return errors.New("valid peer and positive unbond capacity required")
	}
	current := s.BondedCapacityByPeer[peerID]
	if amount > current {
		return errors.New("unbond exceeds bonded provider capacity")
	}
	if s.ReservedByPeer[peerID] != 0 {
		return errors.New("cannot unbond provider while compute is reserved")
	}
	remaining := current - amount
	if remaining == 0 {
		delete(s.BondedCapacityByPeer, peerID)
	} else {
		s.BondedCapacityByPeer[peerID] = remaining
	}
	return nil
}
