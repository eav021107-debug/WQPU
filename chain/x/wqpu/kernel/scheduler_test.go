package kernel

import (
	"math"
	"testing"
)

const testModel = "model-sha256:abc"

func provider(wallet, peer string, capacity, busy, memory uint64) Provider {
	return Provider{
		Wallet:          wallet,
		PeerID:          peer,
		Endpoints:       []string{"wqpu://" + peer + ":7443"},
		ModelHashes:     []string{testModel},
		CapacityUnits:   capacity,
		BusyUnits:       busy,
		FreeMemoryBytes: memory,
		HeartbeatHeight: 10,
		ExpiresHeight:   100,
		CapabilityHash:  "cap-" + peer,
		ProtocolVersion: ProtocolVersion,
	}
}

func TestGlobalPriceBounds(t *testing.T) {
	high, err := AggregatePriceState(100, 95, 1000, 1)
	if err != nil || high.PricePerMillionUnits != 1050 {
		t.Fatalf("high demand price = %+v, err=%v", high, err)
	}
	low, err := AggregatePriceState(100, 10, 1000, 2)
	if err != nil || low.PricePerMillionUnits != 950 {
		t.Fatalf("low demand price = %+v, err=%v", low, err)
	}
	none, err := AggregatePriceState(0, 0, 1000, 3)
	if err != nil || none.PricePerMillionUnits != 1050 {
		t.Fatalf("zero capacity price = %+v, err=%v", none, err)
	}
}

func TestNegativePriceDivisionMatchesPythonFloor(t *testing.T) {
	state, err := AggregatePriceState(10_000, 6_999, 1000, 1)
	if err != nil {
		t.Fatal(err)
	}
	// utilization=6999, deviation=-1, Python -1//4=-1.
	if state.PricePerMillionUnits != 999 {
		t.Fatalf("price=%d, want 999", state.PricePerMillionUnits)
	}
}

func TestSelectsMultipleLeastBusyWorkers(t *testing.T) {
	providers := []Provider{
		provider("busy", "p1", 100, 80, 900),
		provider("free-a", "p2", 100, 10, 700),
		provider("free-b", "p3", 100, 20, 700),
	}
	plan, err := SelectLeastBusy(providers, testModel, 1000, 20, 3, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Allocations) != 2 {
		t.Fatalf("allocations=%d, want 2", len(plan.Allocations))
	}
	if plan.Allocations[0].PeerID != "p2" || plan.Allocations[1].PeerID != "p3" {
		t.Fatalf("unexpected order: %+v", plan.Allocations)
	}
	if plan.AssignedModelBytes() != 1000 {
		t.Fatalf("assigned=%d", plan.AssignedModelBytes())
	}
}

func TestChainReservationIsLoadFloor(t *testing.T) {
	providers := []Provider{
		provider("a", "p1", 100, 5, 900),
		provider("b", "p2", 100, 20, 900),
	}
	plan, err := SelectLeastBusy(
		providers,
		testModel,
		100,
		20,
		2,
		map[string]uint64{"p1": 90},
	)
	if err != nil {
		t.Fatal(err)
	}
	if plan.Allocations[0].PeerID != "p2" {
		t.Fatalf("reserved p1 should not look freer: %+v", plan.Allocations)
	}
}

func TestExpiredProviderIsIgnored(t *testing.T) {
	old := provider("old", "p1", 100, 0, 5000)
	old.ExpiresHeight = 15
	live := provider("live", "p2", 100, 20, 5000)
	plan, err := SelectLeastBusy([]Provider{old, live}, testModel, 100, 20, 2, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Allocations) != 1 || plan.Allocations[0].PeerID != "p2" {
		t.Fatalf("unexpected allocations: %+v", plan.Allocations)
	}
}

func TestChargeRoundsUpAndRejectsOverflow(t *testing.T) {
	v, err := ChargeForUnits(1000, 1)
	if err != nil || v != 1 {
		t.Fatalf("tiny charge=%d err=%v", v, err)
	}
	v, err = ChargeForUnits(1000, 1_000_000)
	if err != nil || v != 1000 {
		t.Fatalf("full charge=%d err=%v", v, err)
	}
	if _, err := ChargeForUnits(math.MaxUint64, math.MaxUint64); err == nil {
		t.Fatal("expected overflow error")
	}
}
