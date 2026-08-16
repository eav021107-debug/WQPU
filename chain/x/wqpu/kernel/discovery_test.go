package kernel

import "testing"

func TestActiveProvidersComeFromChainState(t *testing.T) {
	s, err := NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	for _, wallet := range []string{"wallet-b", "wallet-a"} {
		d := stateSession(wallet, SessionPermProvider)
		if err := s.AuthorizeSession(d); err != nil {
			t.Fatal(err)
		}
		peer := "peer-" + wallet
		if err := s.PublishProvider(wallet, d.SessionAddress, stateProvider(wallet, peer, 0, 20)); err != nil {
			t.Fatal(err)
		}
	}
	providers := s.ActiveProviders(testModel)
	if len(providers) != 2 {
		t.Fatalf("providers=%d", len(providers))
	}
	if providers[0].Wallet != "wallet-a" || providers[1].Wallet != "wallet-b" {
		t.Fatalf("provider order=%+v", providers)
	}
}

func TestPlanModelAvoidsChainReservedPeer(t *testing.T) {
	s, err := NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	for _, item := range []struct {
		wallet string
		peer   string
		busy   uint64
	}{
		{"wallet-a", "peer-a", 0},
		{"wallet-b", "peer-b", 20},
	} {
		d := stateSession(item.wallet, SessionPermProvider)
		if err := s.AuthorizeSession(d); err != nil {
			t.Fatal(err)
		}
		p := stateProvider(item.wallet, item.peer, 0, 20)
		p.BusyUnits = item.busy
		if err := s.PublishProvider(item.wallet, d.SessionAddress, p); err != nil {
			t.Fatal(err)
		}
	}
	s.ReservedByPeer["peer-a"] = 90
	plan, err := s.PlanModel(testModel, 1000, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(plan.Allocations) == 0 || plan.Allocations[0].PeerID != "peer-b" {
		t.Fatalf("plan=%+v", plan)
	}
}
