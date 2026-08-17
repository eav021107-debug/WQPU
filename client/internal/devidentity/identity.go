package devidentity

import (
	"errors"
	"fmt"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

// These identities exist only for the isolated WQPU devnet and CI. They are
// deliberately public and must never hold real funds or be used on mainnet.
const SlotCount = 8

var sessionKeyHexes = [SlotCount]string{
	"0000000000000000000000000000000000000000000000000000000000000001",
	"0000000000000000000000000000000000000000000000000000000000000002",
	"0000000000000000000000000000000000000000000000000000000000000003",
	"0000000000000000000000000000000000000000000000000000000000000004",
	"0000000000000000000000000000000000000000000000000000000000000005",
	"0000000000000000000000000000000000000000000000000000000000000006",
	"0000000000000000000000000000000000000000000000000000000000000007",
	"0000000000000000000000000000000000000000000000000000000000000008",
}

func ValidSlot(slot int) bool { return slot >= 0 && slot < SlotCount }

func PeerID(slot int) common.Hash {
	if !ValidSlot(slot) { return common.Hash{} }
	return crypto.Keccak256Hash([]byte(fmt.Sprintf("wqpu-live-compute-peer-%d", slot+1)))
}

func SessionKey(slot int) (*sessionkey.Key, error) {
	if !ValidSlot(slot) { return nil, errors.New("WQPU devnet identity slot is outside 0..7") }
	private, err := crypto.HexToECDSA(sessionKeyHexes[slot])
	if err != nil { return nil, err }
	return sessionkey.FromPrivateKey(private)
}

func SessionAddress(slot int) (common.Address, error) {
	if !ValidSlot(slot) { return common.Address{}, errors.New("WQPU devnet identity slot is outside 0..7") }
	private, err := crypto.HexToECDSA(sessionKeyHexes[slot])
	if err != nil { return common.Address{}, err }
	return crypto.PubkeyToAddress(private.PublicKey), nil
}

func RPCPort(slot int) int {
	if !ValidSlot(slot) { return 0 }
	return 50052 + slot
}

func ProviderPort(slot int) int {
	if !ValidSlot(slot) { return 0 }
	return 17443 + slot
}
