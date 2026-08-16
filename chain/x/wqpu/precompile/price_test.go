package precompile

import (
	"testing"

	legacykernel "github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func TestBondedPriceCurveMatchesReferenceKernel(t *testing.T) {
	vectors := []struct {
		capacity uint64
		reserved uint64
		previous uint64
		epoch uint64
	}{
		{0, 0, 1000, 1},
		{100, 0, 1000, 2},
		{100, 70, 1000, 3},
		{100, 95, 1000, 4},
		{100, 100, 1000, 5},
		{1_000_000, 123_456, 987_654, 6},
	}
	for _, vector := range vectors {
		got, err := ComputeBondedPrice(vector.capacity, vector.reserved, vector.previous, vector.epoch)
		if err != nil { t.Fatal(err) }
		want, err := legacykernel.AggregatePriceState(vector.capacity, vector.reserved, vector.previous, vector.epoch)
		if err != nil { t.Fatal(err) }
		if got.Epoch != want.Epoch || got.PricePerMillionUnits != want.PricePerMillionUnits || got.BondedCapacityUnits != want.AggregateCapacityUnits || got.ReservedDemandUnits != want.AggregateBusyUnits {
			t.Fatalf("vector=%+v native=%+v reference=%+v", vector, got, want)
		}
	}
}

func TestHugeUnbondedProviderCannotPushGlobalPriceDown(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 1_000_000_000_000)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	price, err := CloseBondedPriceEpoch(state, PriceEpochBlocks)
	if err != nil { t.Fatal(err) }
	if price.BondedCapacityUnits != 0 {
		t.Fatalf("fake advertised supply entered price denominator: %d", price.BondedCapacityUnits)
	}
	if price.PricePerMillionUnits != 1050 {
		t.Fatalf("unbonded fake supply changed expected no-supply price: %d", price.PricePerMillionUnits)
	}
}

func TestBondedProviderCanLegitimatelyIncreasePriceSupply(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 1_000)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	if err := AddProviderBondCapacity(state, provider.PeerID, 1_000); err != nil { t.Fatal(err) }
	price, err := CloseBondedPriceEpoch(state, PriceEpochBlocks)
	if err != nil { t.Fatal(err) }
	if price.BondedCapacityUnits != 1_000 { t.Fatalf("bonded capacity=%d", price.BondedCapacityUnits) }
	if price.PricePerMillionUnits != 950 {
		t.Fatalf("idle bonded supply should lower price by the epoch cap, got %d", price.PricePerMillionUnits)
	}
}

func TestPriceEpochCannotBeClosedTwiceAtSameHeight(t *testing.T) {
	state := newMemoryState()
	if _, err := CloseBondedPriceEpoch(state, PriceEpochBlocks); err != nil { t.Fatal(err) }
	if _, err := CloseBondedPriceEpoch(state, PriceEpochBlocks); err == nil {
		t.Fatal("same price epoch must not be applied twice")
	}
}
