package kernel

import (
	"errors"
	"math/big"
	"sort"
)

func mulDivFloor(a, b, d uint64) (uint64, error) {
	if d == 0 {
		return 0, errors.New("division by zero")
	}
	n := new(big.Int).Mul(new(big.Int).SetUint64(a), new(big.Int).SetUint64(b))
	n.Quo(n, new(big.Int).SetUint64(d))
	if !n.IsUint64() {
		return 0, errors.New("uint64 overflow")
	}
	return n.Uint64(), nil
}

func mulDivCeil(a, b, d uint64) (uint64, error) {
	if d == 0 {
		return 0, errors.New("division by zero")
	}
	n := new(big.Int).Mul(new(big.Int).SetUint64(a), new(big.Int).SetUint64(b))
	q, r := new(big.Int), new(big.Int)
	q.QuoRem(n, new(big.Int).SetUint64(d), r)
	if r.Sign() != 0 {
		q.Add(q, big.NewInt(1))
	}
	if !q.IsUint64() {
		return 0, errors.New("uint64 overflow")
	}
	return q.Uint64(), nil
}

// floorDivSigned matches Python's // semantics for consensus math.
func floorDivSigned(n, d int64) int64 {
	if d <= 0 {
		panic("floorDivSigned requires a positive divisor")
	}
	q := n / d
	if n < 0 && n%d != 0 {
		q--
	}
	return q
}

func AggregatePriceState(capacity, reserved, previousPrice, nextEpoch uint64) (GlobalPriceState, error) {
	if previousPrice == 0 {
		return GlobalPriceState{}, errors.New("previous price must be positive")
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
		utilization, err = mulDivFloor(busy, BasisPoints, capacity)
		if err != nil {
			return GlobalPriceState{}, err
		}
		if utilization > BasisPoints {
			utilization = BasisPoints
		}
	}

	deviation := int64(utilization) - int64(TargetUtilizationBPS)
	move := floorDivSigned(deviation, 4)
	if move > int64(MaxPriceMoveBPS) {
		move = int64(MaxPriceMoveBPS)
	}
	if move < -int64(MaxPriceMoveBPS) {
		move = -int64(MaxPriceMoveBPS)
	}

	factor := int64(BasisPoints) + move
	if factor <= 0 {
		return GlobalPriceState{}, errors.New("invalid price factor")
	}
	price, err := mulDivFloor(previousPrice, uint64(factor), BasisPoints)
	if err != nil {
		return GlobalPriceState{}, err
	}
	if price < MinPricePerMillion {
		price = MinPricePerMillion
	}

	return GlobalPriceState{
		Epoch:                  nextEpoch,
		PricePerMillionUnits:   price,
		AggregateCapacityUnits: capacity,
		AggregateBusyUnits:     busy,
	}, nil
}

func ChargeForUnits(pricePerMillion, computeUnits uint64) (uint64, error) {
	if pricePerMillion == 0 {
		return 0, errors.New("price must be positive")
	}
	if computeUnits == 0 {
		return 0, nil
	}
	return mulDivCeil(pricePerMillion, computeUnits, 1_000_000)
}

func containsModel(models []string, wanted string) bool {
	for _, model := range models {
		if model == wanted {
			return true
		}
	}
	return false
}

func effectiveBusy(p Provider, reserved map[string]uint64) uint64 {
	busy := p.BusyUnits
	if r := reserved[p.PeerID]; r > busy {
		busy = r
	}
	if busy > p.CapacityUnits {
		busy = p.CapacityUnits
	}
	return busy
}

func effectiveFree(p Provider, reserved map[string]uint64) uint64 {
	busy := effectiveBusy(p, reserved)
	if busy >= p.CapacityUnits {
		return 0
	}
	return p.CapacityUnits - busy
}

func effectiveUtilization(p Provider, reserved map[string]uint64) uint64 {
	if p.CapacityUnits == 0 {
		return BasisPoints
	}
	v, err := mulDivFloor(effectiveBusy(p, reserved), BasisPoints, p.CapacityUnits)
	if err != nil || v > BasisPoints {
		return BasisPoints
	}
	return v
}

func usableMemory(p Provider) uint64 {
	headroom, err := mulDivFloor(p.FreeMemoryBytes, ModelHeadroomBPS, BasisPoints)
	if err != nil || headroom >= p.FreeMemoryBytes {
		return 0
	}
	return p.FreeMemoryBytes - headroom
}

func SelectLeastBusy(
	providers []Provider,
	modelHash string,
	modelBytes uint64,
	atHeight uint64,
	maxWorkers int,
	reservedByPeer map[string]uint64,
) (SchedulePlan, error) {
	if modelHash == "" {
		return SchedulePlan{}, errors.New("model hash must be non-empty")
	}
	if modelBytes == 0 {
		return SchedulePlan{}, errors.New("model size must be positive")
	}
	if maxWorkers <= 0 {
		return SchedulePlan{}, errors.New("max workers must be positive")
	}
	if reservedByPeer == nil {
		reservedByPeer = map[string]uint64{}
	}

	candidates := make([]Provider, 0, len(providers))
	for _, p := range providers {
		if p.Validate(atHeight) != nil {
			continue
		}
		if !containsModel(p.ModelHashes, modelHash) {
			continue
		}
		if effectiveFree(p, reservedByPeer) == 0 || usableMemory(p) == 0 {
			continue
		}
		candidates = append(candidates, p)
	}

	sort.SliceStable(candidates, func(i, j int) bool {
		a, b := candidates[i], candidates[j]
		au, bu := effectiveUtilization(a, reservedByPeer), effectiveUtilization(b, reservedByPeer)
		if au != bu {
			return au < bu
		}
		af, bf := effectiveFree(a, reservedByPeer), effectiveFree(b, reservedByPeer)
		if af != bf {
			return af > bf
		}
		am, bm := usableMemory(a), usableMemory(b)
		if am != bm {
			return am > bm
		}
		if a.Wallet != b.Wallet {
			return a.Wallet < b.Wallet
		}
		return a.PeerID < b.PeerID
	})

	if len(candidates) > maxWorkers {
		candidates = candidates[:maxWorkers]
	}

	remaining := modelBytes
	allocations := make([]WorkerAllocation, 0, len(candidates))
	for _, p := range candidates {
		if remaining == 0 {
			break
		}
		assigned := usableMemory(p)
		if assigned > remaining {
			assigned = remaining
		}
		if assigned == 0 {
			continue
		}
		allocations = append(allocations, WorkerAllocation{
			Wallet:             p.Wallet,
			PeerID:             p.PeerID,
			Endpoint:           p.Endpoints[0],
			AssignedModelBytes: assigned,
			UtilizationBPS:     effectiveUtilization(p, reservedByPeer),
			FreeUnits:          effectiveFree(p, reservedByPeer),
		})
		remaining -= assigned
	}

	if remaining != 0 {
		return SchedulePlan{}, errors.New("insufficient compatible free memory")
	}

	return SchedulePlan{
		ModelHash:       modelHash,
		TotalModelBytes: modelBytes,
		Allocations:     allocations,
	}, nil
}
