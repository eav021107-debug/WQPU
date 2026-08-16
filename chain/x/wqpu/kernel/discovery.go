package kernel

import "sort"

// ActiveProviders is the deterministic chain view used by new WQPU clients for
// discovery. No invite code or provider-specific price is involved.
func (s *State) ActiveProviders(modelHash string) []Provider {
	if s == nil {
		return nil
	}
	out := make([]Provider, 0, len(s.Providers))
	for _, p := range s.Providers {
		if p.Validate(s.Height) != nil {
			continue
		}
		if modelHash != "" && !containsModel(p.ModelHashes, modelHash) {
			continue
		}
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Wallet != out[j].Wallet {
			return out[i].Wallet < out[j].Wallet
		}
		return out[i].PeerID < out[j].PeerID
	})
	return out
}

// PlanModel uses the same chain snapshot every requester sees. The requester
// still coordinates its own inference, but provider choice is deterministic and
// least-busy with confirmed reservations as the minimum load floor.
func (s *State) PlanModel(modelHash string, modelBytes uint64, maxWorkers int) (SchedulePlan, error) {
	return SelectLeastBusy(
		s.ActiveProviders(modelHash),
		modelHash,
		modelBytes,
		s.Height,
		maxWorkers,
		s.ReservedByPeer,
	)
}
