package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

type occupiedContract struct{}

func (occupiedContract) Address() common.Address { return Address }
func (occupiedContract) Name() string { return "occupied" }
func (occupiedContract) RequiredGas([]byte) uint64 { return 1 }
func (occupiedContract) Run(*vm.EVM, *vm.Contract, bool) ([]byte, error) { return nil, nil }

func TestWithWQPURegistersDedicatedAddress(t *testing.T) {
	precompiles := WithWQPU(nil)
	contract, ok := precompiles[Address]
	if !ok {
		t.Fatal("WQPU precompile was not registered")
	}
	if contract.Name() != Name || contract.Address() != Address {
		t.Fatalf("registered contract=%s %s", contract.Name(), contract.Address().Hex())
	}
}

func TestWithWQPURefusesAddressCollision(t *testing.T) {
	precompiles := map[common.Address]vm.PrecompiledContract{Address: occupiedContract{}}
	defer func() {
		if recover() == nil {
			t.Fatal("address collision should panic during deterministic startup")
		}
	}()
	_ = WithWQPU(precompiles)
}
