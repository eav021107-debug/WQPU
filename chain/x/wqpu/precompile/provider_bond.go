package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

// One price-capacity unit requires one protocol payment unit of provider bond.
// A payment unit is 1e-9 WQPU (see escrow.go). This conversion is consensus
// data for protocol v1; changing it requires a protocol upgrade.
const ProviderBondPaymentUnitsPerCapacityUnit uint64 = 1

func providerBondKey(peerID common.Hash) []byte { return peerID.Bytes() }

func ProviderBondedCapacityUnits(state WordState, peerID common.Hash) (uint64, error) {
	if state == nil || peerID == (common.Hash{}) {
		return 0, errors.New("valid WQPU state and peer id required")
	}
	return GetUint64(state, "provider-bond-capacity", providerBondKey(peerID))
}

func ProviderBondPaymentUnits(capacityUnits uint64) (uint64, error) {
	if capacityUnits == 0 {
		return 0, errors.New("WQPU provider bond capacity must be positive")
	}
	if capacityUnits > ^uint64(0)/ProviderBondPaymentUnitsPerCapacityUnit {
		return 0, errors.New("WQPU provider bond conversion overflow")
	}
	return capacityUnits * ProviderBondPaymentUnitsPerCapacityUnit, nil
}

// AddProviderBondCapacity records additional native-WQPU-backed price capacity.
// The transaction boundary must verify and retain the corresponding native
// value before this function is called.
func AddProviderBondCapacity(state WordState, peerID common.Hash, additionalCapacity uint64) error {
	if additionalCapacity == 0 {
		return errors.New("WQPU provider bond addition must be positive")
	}
	provider, exists, err := LoadPeerProvider(state, peerID)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("cannot bond unknown WQPU peer")
	}
	current, err := ProviderBondedCapacityUnits(state, peerID)
	if err != nil {
		return err
	}
	if additionalCapacity > ^uint64(0)-current {
		return errors.New("WQPU provider bond capacity overflow")
	}
	updated := current + additionalCapacity
	// Bond above currently advertised capacity is rejected rather than silently
	// accepting capital that cannot influence price.
	if updated > provider.CapacityUnits {
		return errors.New("WQPU provider bond exceeds advertised peer capacity")
	}
	return SetUint64(state, "provider-bond-capacity", providerBondKey(peerID), updated)
}

// RemoveProviderBondCapacity releases price capacity only when the peer is not
// carrying reserved work. The transaction boundary performs the native refund.
func RemoveProviderBondCapacity(state WordState, peerID common.Hash, capacityUnits uint64) error {
	if capacityUnits == 0 {
		return errors.New("WQPU provider unbond capacity must be positive")
	}
	current, err := ProviderBondedCapacityUnits(state, peerID)
	if err != nil {
		return err
	}
	if capacityUnits > current {
		return errors.New("WQPU provider unbond exceeds bonded capacity")
	}
	reserved, err := ReservedPeerUnits(state, peerID)
	if err != nil {
		return err
	}
	if reserved != 0 {
		return errors.New("cannot unbond WQPU provider while compute is reserved")
	}
	return SetUint64(state, "provider-bond-capacity", providerBondKey(peerID), current-capacityUnits)
}

func ProviderPriceCapacityUnits(state WordState, peerID common.Hash, height uint64) (uint64, error) {
	provider, exists, err := LoadPeerProvider(state, peerID)
	if err != nil {
		return 0, err
	}
	if !exists || !provider.ActiveAt(height) {
		return 0, nil
	}
	bonded, err := ProviderBondedCapacityUnits(state, peerID)
	if err != nil {
		return 0, err
	}
	if bonded > provider.CapacityUnits {
		return provider.CapacityUnits, nil
	}
	return bonded, nil
}

func AggregateBondedPriceCapacity(state WordState, height uint64) (uint64, error) {
	peers, err := ProviderPeerIDs(state)
	if err != nil {
		return 0, err
	}
	var total uint64
	for _, peerID := range peers {
		capacity, err := ProviderPriceCapacityUnits(state, peerID, height)
		if err != nil {
			return 0, err
		}
		if capacity > ^uint64(0)-total {
			return 0, errors.New("WQPU aggregate bonded capacity overflow")
		}
		total += capacity
	}
	return total, nil
}
