package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

func reserveJobInput(t *testing.T, envelope JobReserveEnvelope) []byte {
	t.Helper()
	raw, err := EncodeJobReserveEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	args := append(abiUintWord(32), encodeDynamicBytes(raw)[32:]...)
	input := append([]byte{}, selectorReserveJob[:]...)
	return append(input, args...)
}

func TestReserveJobABIRoundTrip(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "native-job")
	input := reserveJobInput(t, envelope)
	decoded, err := decodeReserveJob(input)
	if err != nil { t.Fatal(err) }
	if decoded.Wallet != envelope.Wallet || decoded.Session != envelope.Session || decoded.Request.JobID != envelope.Request.JobID {
		t.Fatalf("decoded=%+v", decoded)
	}
}

func TestReserveJobABIRejectsNonCanonicalTrailingBytes(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "native-job")
	input := append(reserveJobInput(t, envelope), 0)
	if _, err := decodeReserveJob(input); err == nil { t.Fatal("reserveJob trailing bytes should fail") }
}

func TestJobContractGasAndRegistration(t *testing.T) {
	contract := NewJobNetworkContract(DevNetworkConfig)
	if gas := contract.RequiredGas(selectorReserveJob[:]); gas != reserveJobGas { t.Fatalf("reserveJob gas=%d", gas) }
	precompiles := WithWQPUJobNetwork(nil)
	registered, ok := precompiles[Address]
	if !ok { t.Fatal("job WQPU contract missing") }
	if _, ok := registered.(JobNetworkContract); !ok { t.Fatalf("unexpected contract type %T", registered) }
}

func TestJobContractRefusesAddressCollision(t *testing.T) {
	precompiles := map[common.Address]vm.PrecompiledContract{Address: occupiedContract{}}
	defer func() { if recover() == nil { t.Fatal("job WQPU registration should reject collision") } }()
	_ = WithWQPUJobNetwork(precompiles)
}
