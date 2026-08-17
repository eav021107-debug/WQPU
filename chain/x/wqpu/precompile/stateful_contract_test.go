package precompile

import (
	"encoding/hex"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/ethereum/go-ethereum/crypto"
)

func abiAddressWord(address common.Address) []byte {
	return common.LeftPadBytes(address.Bytes(), 32)
}

func abiUintWord(value uint64) []byte { return encodeUint256(value) }

func authorizeInput(t *testing.T, d SessionDelegation, signatureHex string) []byte {
	t.Helper()
	sig, err := hex.DecodeString(signatureHex[2:])
	if err != nil { t.Fatal(err) }
	head := make([]byte, 0, 9*32)
	head = append(head, abiAddressWord(d.Wallet)...)
	head = append(head, abiAddressWord(d.Session)...)
	head = append(head, abiUintWord(d.IssuedHeight)...)
	head = append(head, abiUintWord(d.ExpiresHeight)...)
	head = append(head, abiUintWord(d.MaxSpendUnits)...)
	head = append(head, abiUintWord(d.MaxJobUnits)...)
	head = append(head, abiUintWord(d.RevocationNonce)...)
	head = append(head, abiUintWord(d.Permissions)...)
	head = append(head, abiUintWord(uint64(9*32))...)
	tail := append(abiUintWord(uint64(len(sig))), sig...)
	for len(tail)%32 != 0 { tail = append(tail, 0) }
	input := append([]byte{}, selectorAuthorizeSession[:]...)
	input = append(input, head...)
	return append(input, tail...)
}

func TestAuthorizeSessionABIRoundTrip(t *testing.T) {
	d, sig := testSessionDelegation(t)
	input := authorizeInput(t, d, sig)
	decoded, decodedSig, err := decodeAuthorizeSession(input, DevNetworkConfig)
	if err != nil { t.Fatal(err) }
	if decoded.Wallet != d.Wallet || decoded.Session != d.Session ||
		decoded.IssuedHeight != d.IssuedHeight || decoded.ExpiresHeight != d.ExpiresHeight ||
		decoded.MaxSpendUnits != d.MaxSpendUnits || decoded.MaxJobUnits != d.MaxJobUnits ||
		decoded.RevocationNonce != d.RevocationNonce || decoded.Permissions != d.Permissions {
		t.Fatalf("decoded=%+v want=%+v", decoded, d)
	}
	if decodedSig != sig {
		t.Fatalf("signature=%s want=%s", decodedSig, sig)
	}
	if err := VerifySessionWalletSignature(decoded, DevNetworkConfig.EVMChainID, decodedSig); err != nil {
		t.Fatal(err)
	}
}

func TestAuthorizeSessionRejectsNonCanonicalPadding(t *testing.T) {
	d, sig := testSessionDelegation(t)
	input := authorizeInput(t, d, sig)
	input[len(input)-1] = 1
	if _, _, err := decodeAuthorizeSession(input, DevNetworkConfig); err == nil {
		t.Fatal("non-zero ABI padding should fail")
	}
}

func TestAuthorizeSessionRejectsOffsetIntoHead(t *testing.T) {
	d, sig := testSessionDelegation(t)
	input := authorizeInput(t, d, sig)
	args := input[4:]
	copy(args[8*32:9*32], abiUintWord(32))
	if _, _, err := decodeAuthorizeSession(input, DevNetworkConfig); err == nil {
		t.Fatal("dynamic signature offset into ABI head should fail")
	}
}

func TestAuthorizeSessionRejectsWrongSignatureLength(t *testing.T) {
	d, sig := testSessionDelegation(t)
	input := authorizeInput(t, d, sig)
	args := input[4:]
	offset := 9 * 32
	copy(args[offset:offset+32], abiUintWord(64))
	// Rebuild exact canonical length for a 64-byte payload so the failure is the
	// protocol signature length, not generic trailing-byte validation.
	input = input[:4+offset+32+64]
	if _, _, err := decodeAuthorizeSession(input, DevNetworkConfig); err == nil {
		t.Fatal("64-byte wallet signature should fail")
	}
}

func TestStatefulContractRegistrationAndGas(t *testing.T) {
	precompiles := WithWQPUStatefulNetwork(nil)
	contract, ok := precompiles[Address]
	if !ok { t.Fatal("stateful WQPU precompile missing") }
	if _, ok := contract.(StatefulNetworkContract); !ok {
		t.Fatalf("unexpected contract type %T", contract)
	}
	if gas := contract.RequiredGas(selectorAuthorizeSession[:]); gas != authorizeSessionGas {
		t.Fatalf("authorize gas=%d", gas)
	}
}

func TestStatefulContractRefusesAddressCollision(t *testing.T) {
	precompiles := map[common.Address]vm.PrecompiledContract{Address: occupiedContract{}}
	defer func() {
		if recover() == nil { t.Fatal("stateful WQPU registration should reject collision") }
	}()
	_ = WithWQPUStatefulNetwork(precompiles)
}

func TestAuthorizeSignatureCannotBeReusedForChangedLimits(t *testing.T) {
	d, sig := testSessionDelegation(t)
	input := authorizeInput(t, d, sig)
	decoded, decodedSig, err := decodeAuthorizeSession(input, DevNetworkConfig)
	if err != nil { t.Fatal(err) }
	decoded.MaxSpendUnits++
	if err := VerifySessionWalletSignature(decoded, DevNetworkConfig.EVMChainID, decodedSig); err == nil {
		t.Fatal("changed spend limit should invalidate wallet proof")
	}
}

func TestSelectorIsBoundToExactABI(t *testing.T) {
	if selectorAuthorizeSession == methodSelector("authorizeSession(address,address,uint256,uint64,uint64,uint64,uint64,uint64,bytes)") {
		t.Fatal("selector unexpectedly ignores ABI type changes")
	}
	if selectorAuthorizeSession == [4]byte{} {
		t.Fatal("empty authorize selector")
	}
	_ = crypto.Keccak256 // ensure this test package uses the same geth crypto module as runtime
}
