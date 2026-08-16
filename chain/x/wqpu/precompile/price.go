package precompile

import (
	"errors"
	"math/big"
)

const (
	PriceTargetUtilizationBPS uint64 = 7_000
	PriceMaxMoveBPS          uint64 = 500
	PriceMinPerMillion       uint64 = 1
	PriceEpochBlocks         uint64 = 20
)

type BondedPriceState struct {
	Epoch                  uint64
	PricePerMillionUnits   uint64
	BondedCapacityUnits    uint64
	ReservedDemandUnits    uint64
}

func priceMulDivFloor(a, b, d uint64) (uint64, error) {
	if d == 0 {
		return 0, errors.New("WQPU price division by zero")
	}
	n := new(big.Int).Mul(new(big.Int).SetUint64(a), new(big.Int).SetUint64(b))
	n.Quo(n, new(big.Int).SetUint64(d))
	if !n.IsUint64() {
		return 0, errors.New("WQPU price uint64 overflow")
	}
	return n.Uint64(), nil
}

func priceFloorDivSigned(n, d int64) int64 {
	if d <= 0 {
		panic("priceFloorDivSigned requires positive divisor")
	}
	q := n / d
	if n < 0 && n%d != 0 {
		q--
	}
	return q
}

// ComputeBondedPrice reproduces the reference-kernel price curve, but its
// capacity input is explicitly bond-backed capacity, never raw provider claims.
func ComputeBondedPrice(capacity, reserved, previousPrice, epoch uint64) (BondedPriceState, error) {
	if previousPrice == 0 {
		return BondedPriceState{}, errors.New("previous WQPU price must be positive")
	}
	busy := reserved
	if capacity == 0 {
		busy = 0
	} else if busy > capacity {
		busy = capacity
	}
	utilization := BasisPoints
	if capacity > 0 {
		var err error
		utilization, err = priceMulDivFloor(busy, BasisPoints, capacity)
		if err != nil {
			return BondedPriceState{}, err
		}
		if utilization > BasisPoints {
			utilization = BasisPoints
		}
	}
	deviation := int64(utilization) - int64(PriceTargetUtilizationBPS)
	move := priceFloorDivSigned(deviation, 4)
	if move > int64(PriceMaxMoveBPS) {
		move = int64(PriceMaxMoveBPS)
	}
	if move < -int64(PriceMaxMoveBPS) {
		move = -int64(PriceMaxMoveBPS)
	}
	factor := int64(BasisPoints) + move
	if factor <= 0 {
		return BondedPriceState{}, errors.New("invalid WQPU price factor")
	}
	price, err := priceMulDivFloor(previousPrice, uint64(factor), BasisPoints)
	if err != nil {
		return BondedPriceState{}, err
	}
	if price < PriceMinPerMillion {
		price = PriceMinPerMillion
	}
	return BondedPriceState{
		Epoch: epoch,
		PricePerMillionUnits: price,
		BondedCapacityUnits: capacity,
		ReservedDemandUnits: busy,
	}, nil
}

func AggregateReservedDemand(state WordState, height uint64) (uint64, error) {
	peers, err := ProviderPeerIDs(state)
	if err != nil {
		return 0, err
	}
	var total uint64
	for _, peerID := range peers {
		provider, exists, err := LoadPeerProvider(state, peerID)
		if err != nil {
			return 0, err
		}
		if !exists || !provider.ActiveAt(height) {
			continue
		}
		reserved, err := ReservedPeerUnits(state, peerID)
		if err != nil {
			return 0, err
		}
		if reserved > ^uint64(0)-total {
			return 0, errors.New("WQPU aggregate reserved demand overflow")
		}
		total += reserved
	}
	return total, nil
}

func PriceEpochAtHeight(height uint64) uint64 {
	return height / PriceEpochBlocks
}

func CloseBondedPriceEpoch(state WordState, height uint64) (BondedPriceState, error) {
	if state == nil {
		return BondedPriceState{}, errors.New("nil WQPU state")
	}
	nextEpoch := PriceEpochAtHeight(height)
	storedEpoch, err := GetUint64(state, "global", []byte("price-epoch"))
	if err != nil {
		return BondedPriceState{}, err
	}
	if nextEpoch == 0 || nextEpoch <= storedEpoch {
		return BondedPriceState{}, errors.New("WQPU price epoch is not ready to close")
	}
	capacity, err := AggregateBondedPriceCapacity(state, height)
	if err != nil {
		return BondedPriceState{}, err
	}
	reserved, err := AggregateReservedDemand(state, height)
	if err != nil {
		return BondedPriceState{}, err
	}
	previous, err := currentGlobalPrice(state)
	if err != nil {
		return BondedPriceState{}, err
	}
	next, err := ComputeBondedPrice(capacity, reserved, previous, nextEpoch)
	if err != nil {
		return BondedPriceState{}, err
	}
	if err := SetUint64(state, "global", []byte("price-per-million"), next.PricePerMillionUnits); err != nil {
		return BondedPriceState{}, err
	}
	if err := SetUint64(state, "global", []byte("price-epoch"), next.Epoch); err != nil {
		return BondedPriceState{}, err
	}
	return next, nil
}
