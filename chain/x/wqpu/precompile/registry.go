package precompile

import (
	"fmt"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

// WithWQPU adds the WQPU native contract to a runtime-local precompile map.
// It deliberately refuses address collisions instead of silently replacing
// another protocol contract.
func WithWQPU(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil {
		existing = make(map[common.Address]vm.PrecompiledContract)
	}
	if current, exists := existing[Address]; exists {
		panic(fmt.Sprintf("WQPU precompile address %s already occupied by %s", Address.Hex(), current.Name()))
	}
	existing[Address] = New()
	return existing
}
