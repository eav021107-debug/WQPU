package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

func publishInput(t *testing.T, envelope ProviderPublishEnvelope) []byte {
	t.Helper()
	raw, err := EncodeProviderPublishEnvelope(envelope)
	if err != nil {
		t.Fatal(err)
	}
	args := append(abiUintWord(32), encodeDynamicBytes(raw)[32:]...)
	input := append([]byte{}, selectorPublishProvider[:]...)
	return append(input, args...)
}

func TestPublishProviderABIRoundTrip(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	input := publishInput(t, envelope)
	decoded, err := decodePublishProvider(input)
	if err != nil {
		t.Fatal(err)
	}
	if decoded.Wallet != envelope.Wallet || decoded.Session != envelope.Session || decoded.ActionNonce != envelope.ActionNonce || decoded.Announcement.PeerID != envelope.Announcement.PeerID {
		t.Fatalf("decoded=%+v", decoded)
	}
}

func TestPublishProviderABIRejectsTrailingBytes(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	input := append(publishInput(t, envelope), 0)
	if _, err := decodePublishProvider(input); err == nil {
		t.Fatal("publishProvider trailing bytes should fail")
	}
}

func TestProviderContractGasAndRegistration(t *testing.T) {
	contract := NewProviderNetworkContract(DevNetworkConfig)
	if gas := contract.RequiredGas(selectorPublishProvider[:]); gas != publishProviderGas {
		t.Fatalf("publishProvider gas=%d", gas)
	}
	precompiles := WithWQPUProviderNetwork(nil)
	registered, ok := precompiles[Address]
	if !ok {
		t.Fatal("provider WQPU contract missing")
	}
	if _, ok := registered.(ProviderNetworkContract); !ok {
		t.Fatalf("unexpected contract type %T", registered)
	}
}

func TestProviderContractRefusesAddressCollision(t *testing.T) {
	precompiles := map[common.Address]vm.PrecompiledContract{Address: occupiedContract{}}
	defer func() {
		if recover() == nil {
			t.Fatal("provider WQPU registration should reject collision")
		}
	}()
	_ = WithWQPUProviderNetwork(precompiles)
}
