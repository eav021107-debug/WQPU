package kernel

import "errors"

const (
	ProtocolVersion       = uint32(1)
	BasisPoints           = uint64(10_000)
	TargetUtilizationBPS  = uint64(7_000)
	MaxPriceMoveBPS       = uint64(500)
	ModelHeadroomBPS      = uint64(1_200)
	MinPricePerMillion    = uint64(1)
	DefaultMaxWorkers     = 8
)

type Provider struct {
	Wallet          string
	PeerID          string
	Endpoints       []string
	ModelHashes     []string
	CapacityUnits   uint64
	BusyUnits       uint64
	FreeMemoryBytes uint64
	HeartbeatHeight uint64
	ExpiresHeight   uint64
	CapabilityHash  string
	ProtocolVersion uint32
}

func (p Provider) Validate(atHeight uint64) error {
	if p.Wallet == "" || p.PeerID == "" || p.CapabilityHash == "" {
		return errors.New("provider identity fields must be non-empty")
	}
	if len(p.Endpoints) == 0 {
		return errors.New("provider must advertise at least one endpoint")
	}
	for _, endpoint := range p.Endpoints {
		if endpoint == "" {
			return errors.New("provider endpoint must be non-empty")
		}
	}
	for _, model := range p.ModelHashes {
		if model == "" {
			return errors.New("model hash must be non-empty")
		}
	}
	if p.BusyUnits > p.CapacityUnits {
		return errors.New("busy units cannot exceed capacity")
	}
	if p.ExpiresHeight <= p.HeartbeatHeight {
		return errors.New("provider expiry must follow heartbeat")
	}
	if atHeight >= p.ExpiresHeight {
		return errors.New("provider record expired")
	}
	if p.ProtocolVersion != ProtocolVersion {
		return errors.New("unsupported protocol version")
	}
	return nil
}

type GlobalPriceState struct {
	Epoch                  uint64
	PricePerMillionUnits   uint64
	AggregateCapacityUnits uint64
	AggregateBusyUnits     uint64
}

type WorkerAllocation struct {
	Wallet             string
	PeerID             string
	Endpoint           string
	AssignedModelBytes uint64
	UtilizationBPS     uint64
	FreeUnits          uint64
}

type SchedulePlan struct {
	ModelHash       string
	TotalModelBytes uint64
	Allocations     []WorkerAllocation
}

func (p SchedulePlan) AssignedModelBytes() uint64 {
	var total uint64
	for _, item := range p.Allocations {
		total += item.AssignedModelBytes
	}
	return total
}
