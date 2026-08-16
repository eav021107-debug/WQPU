package transport

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdh"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"math"
	"strings"
	"sync"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

const (
	ProtocolVersion uint32 = 1
	MaxChainIDBytes        = 64
	MaxPlaintextBytes      = 1 << 20 // tunnel larger streams as ordered chunks
	helloSignatureBytes    = 65
)

var handshakeDomain = []byte("WQPU-TRANSPORT-HANDSHAKE-V1")

// Role is signed into the handshake to prevent reflection of an initiator hello
// back to the initiator.
type Role byte

const (
	RoleInitiator Role = 1
	RoleResponder Role = 2
)

func (r Role) valid() bool { return r == RoleInitiator || r == RoleResponder }
func (r Role) opposite() Role {
	if r == RoleInitiator { return RoleResponder }
	return RoleInitiator
}

// Signer is deliberately smaller than a private-key interface. The WQPU
// transport can authenticate with an in-memory session key without ever gaining
// access to the key bytes.
type Signer interface {
	Address() string
	SignDigest([]byte) ([]byte, error)
}

type Hello struct {
	Version      uint32
	Role         Role
	ChainID      string
	PeerID       [32]byte
	Session      common.Address
	EphemeralPub [32]byte
	Nonce        [32]byte
	Signature    [helloSignatureBytes]byte
}

func (h Hello) validateShape() error {
	if h.Version != ProtocolVersion { return errors.New("unsupported WQPU transport version") }
	if !h.Role.valid() { return errors.New("invalid WQPU transport role") }
	if len(h.ChainID) == 0 || len(h.ChainID) > MaxChainIDBytes { return errors.New("invalid WQPU transport chain id length") }
	if h.PeerID == ([32]byte{}) || h.Session == (common.Address{}) { return errors.New("WQPU transport peer and session are required") }
	if h.EphemeralPub == ([32]byte{}) || h.Nonce == ([32]byte{}) { return errors.New("WQPU transport ephemeral key and nonce are required") }
	return nil
}

func (h Hello) unsignedBytes() ([]byte, error) {
	if err := h.validateShape(); err != nil { return nil, err }
	out := make([]byte, 0, 4+1+1+len(h.ChainID)+32+20+32+32)
	var version [4]byte
	binary.BigEndian.PutUint32(version[:], h.Version)
	out = append(out, version[:]...)
	out = append(out, byte(h.Role), byte(len(h.ChainID)))
	out = append(out, []byte(h.ChainID)...)
	out = append(out, h.PeerID[:]...)
	out = append(out, h.Session.Bytes()...)
	out = append(out, h.EphemeralPub[:]...)
	out = append(out, h.Nonce[:]...)
	return out, nil
}

func (h Hello) Digest() ([]byte, error) {
	body, err := h.unsignedBytes()
	if err != nil { return nil, err }
	return crypto.Keccak256(handshakeDomain, body), nil
}

func (h Hello) MarshalBinary() ([]byte, error) {
	body, err := h.unsignedBytes()
	if err != nil { return nil, err }
	out := make([]byte, 0, len(body)+helloSignatureBytes)
	out = append(out, body...)
	out = append(out, h.Signature[:]...)
	return out, nil
}

func ParseHello(data []byte) (Hello, error) {
	const fixedWithoutChain = 4 + 1 + 1 + 32 + 20 + 32 + 32 + helloSignatureBytes
	if len(data) < fixedWithoutChain+1 || len(data) > fixedWithoutChain+MaxChainIDBytes {
		return Hello{}, errors.New("invalid WQPU transport hello length")
	}
	var h Hello
	pos := 0
	h.Version = binary.BigEndian.Uint32(data[pos:pos+4]); pos += 4
	h.Role = Role(data[pos]); pos++
	chainLen := int(data[pos]); pos++
	if chainLen == 0 || chainLen > MaxChainIDBytes || len(data) != fixedWithoutChain+chainLen {
		return Hello{}, errors.New("invalid WQPU transport chain id encoding")
	}
	h.ChainID = string(data[pos:pos+chainLen]); pos += chainLen
	copy(h.PeerID[:], data[pos:pos+32]); pos += 32
	h.Session = common.BytesToAddress(data[pos:pos+20]); pos += 20
	copy(h.EphemeralPub[:], data[pos:pos+32]); pos += 32
	copy(h.Nonce[:], data[pos:pos+32]); pos += 32
	copy(h.Signature[:], data[pos:pos+helloSignatureBytes]); pos += helloSignatureBytes
	if pos != len(data) { return Hello{}, errors.New("trailing WQPU transport hello bytes") }
	if err := h.validateShape(); err != nil { return Hello{}, err }
	return h, nil
}

func normalizeAddress(value string) (common.Address, error) {
	if !common.IsHexAddress(value) { return common.Address{}, errors.New("invalid EVM session address") }
	addr := common.HexToAddress(value)
	if addr == (common.Address{}) { return common.Address{}, errors.New("zero EVM session address") }
	return addr, nil
}

func VerifyHello(h Hello, expectedRole Role, expectedChainID string, expectedPeerID [32]byte, expectedSession string) error {
	if err := h.validateShape(); err != nil { return err }
	if h.Role != expectedRole { return errors.New("unexpected WQPU transport handshake role") }
	if h.ChainID != expectedChainID { return errors.New("WQPU transport hello belongs to another chain") }
	if h.PeerID != expectedPeerID { return errors.New("WQPU transport hello belongs to another peer") }
	expectedAddr, err := normalizeAddress(expectedSession)
	if err != nil { return err }
	if h.Session != expectedAddr { return errors.New("WQPU transport hello uses an unauthorized control session") }
	digest, err := h.Digest()
	if err != nil { return err }
	recovered, err := sessionkey.RecoverAddress(digest, h.Signature[:])
	if err != nil { return fmt.Errorf("invalid WQPU transport session signature: %w", err) }
	if !strings.EqualFold(recovered, expectedAddr.Hex()) { return errors.New("WQPU transport signature was made by another session") }
	return nil
}

type Handshake struct {
	role      Role
	private   *ecdh.PrivateKey
	hello     Hello
	encoded   []byte
}

func NewHandshake(role Role, signer Signer, chainID string, peerID [32]byte) (*Handshake, error) {
	if !role.valid() { return nil, errors.New("invalid WQPU transport role") }
	if signer == nil { return nil, errors.New("WQPU transport signer is required") }
	session, err := normalizeAddress(signer.Address())
	if err != nil { return nil, err }
	curve := ecdh.X25519()
	private, err := curve.GenerateKey(rand.Reader)
	if err != nil { return nil, err }
	pub := private.PublicKey().Bytes()
	if len(pub) != 32 { return nil, errors.New("unexpected X25519 public key length") }
	var ephemeral [32]byte
	copy(ephemeral[:], pub)
	var nonce [32]byte
	if _, err := rand.Read(nonce[:]); err != nil { return nil, err }
	h := Hello{Version: ProtocolVersion, Role: role, ChainID: chainID, PeerID: peerID, Session: session, EphemeralPub: ephemeral, Nonce: nonce}
	digest, err := h.Digest()
	if err != nil { return nil, err }
	sig, err := signer.SignDigest(digest)
	if err != nil { return nil, err }
	if len(sig) != helloSignatureBytes { return nil, errors.New("WQPU transport signer returned invalid signature length") }
	copy(h.Signature[:], sig)
	encoded, err := h.MarshalBinary()
	if err != nil { return nil, err }
	return &Handshake{role: role, private: private, hello: h, encoded: encoded}, nil
}

func (h *Handshake) Hello() Hello { if h == nil { return Hello{} }; return h.hello }
func (h *Handshake) Bytes() []byte { if h == nil { return nil }; return append([]byte(nil), h.encoded...) }

func hkdfSHA256(secret, salt, info []byte, length int) ([]byte, error) {
	if length <= 0 || length > 255*sha256.Size { return nil, errors.New("invalid HKDF output length") }
	extract := hmac.New(sha256.New, salt)
	_, _ = extract.Write(secret)
	prk := extract.Sum(nil)
	out := make([]byte, 0, length)
	var previous []byte
	for counter := byte(1); len(out) < length; counter++ {
		expand := hmac.New(sha256.New, prk)
		_, _ = expand.Write(previous)
		_, _ = expand.Write(info)
		_, _ = expand.Write([]byte{counter})
		previous = expand.Sum(nil)
		need := length - len(out)
		if need > len(previous) { need = len(previous) }
		out = append(out, previous[:need]...)
	}
	return out, nil
}

func transcriptHash(initiator, responder Hello) ([32]byte, error) {
	i, err := initiator.MarshalBinary(); if err != nil { return [32]byte{}, err }
	r, err := responder.MarshalBinary(); if err != nil { return [32]byte{}, err }
	h := sha256.New()
	_, _ = h.Write([]byte("WQPU-TRANSPORT-TRANSCRIPT-V1"))
	_, _ = h.Write(i)
	_, _ = h.Write(r)
	var out [32]byte
	copy(out[:], h.Sum(nil))
	return out, nil
}

func makeAEAD(key []byte) (cipher.AEAD, error) {
	block, err := aes.NewCipher(key)
	if err != nil { return nil, err }
	return cipher.NewGCM(block)
}

type Channel struct {
	send      cipher.AEAD
	recv      cipher.AEAD
	sendPrefix [4]byte
	recvPrefix [4]byte
	transcript [32]byte
	sendSeq   uint64
	recvSeq   uint64
	sendMu    sync.Mutex
	recvMu    sync.Mutex
}

// Establish verifies the remote chain/peer/control-session binding before
// deriving any traffic keys. expectedRemoteSession must come from verified chain
// registry state, not from the untrusted remote hello itself.
func (h *Handshake) Establish(remote Hello, expectedRemotePeerID [32]byte, expectedRemoteSession string) (*Channel, error) {
	if h == nil || h.private == nil { return nil, errors.New("WQPU transport handshake is unavailable") }
	if err := VerifyHello(remote, h.role.opposite(), h.hello.ChainID, expectedRemotePeerID, expectedRemoteSession); err != nil { return nil, err }
	if remote.PeerID == h.hello.PeerID { return nil, errors.New("WQPU transport cannot connect a peer to itself") }
	curve := ecdh.X25519()
	remotePub, err := curve.NewPublicKey(remote.EphemeralPub[:])
	if err != nil { return nil, errors.New("invalid remote X25519 key") }
	shared, err := h.private.ECDH(remotePub)
	if err != nil { return nil, err }
	var initiator, responder Hello
	if h.role == RoleInitiator { initiator, responder = h.hello, remote } else { initiator, responder = remote, h.hello }
	transcript, err := transcriptHash(initiator, responder)
	if err != nil { return nil, err }
	saltHash := sha256.New()
	_, _ = saltHash.Write([]byte("WQPU-TRANSPORT-SALT-V1"))
	_, _ = saltHash.Write(initiator.Nonce[:])
	_, _ = saltHash.Write(responder.Nonce[:])
	salt := saltHash.Sum(nil)
	i2r, err := hkdfSHA256(shared, salt, append([]byte("wqpu/i2r/"), transcript[:]...), 36)
	if err != nil { return nil, err }
	r2i, err := hkdfSHA256(shared, salt, append([]byte("wqpu/r2i/"), transcript[:]...), 36)
	if err != nil { return nil, err }
	var sendMaterial, recvMaterial []byte
	if h.role == RoleInitiator { sendMaterial, recvMaterial = i2r, r2i } else { sendMaterial, recvMaterial = r2i, i2r }
	sendAEAD, err := makeAEAD(sendMaterial[:32]); if err != nil { return nil, err }
	recvAEAD, err := makeAEAD(recvMaterial[:32]); if err != nil { return nil, err }
	c := &Channel{send: sendAEAD, recv: recvAEAD, transcript: transcript, sendSeq: 1, recvSeq: 1}
	copy(c.sendPrefix[:], sendMaterial[32:36])
	copy(c.recvPrefix[:], recvMaterial[32:36])
	return c, nil
}

func frameNonce(prefix [4]byte, sequence uint64) []byte {
	nonce := make([]byte, 12)
	copy(nonce[:4], prefix[:])
	binary.BigEndian.PutUint64(nonce[4:], sequence)
	return nonce
}

func frameAAD(transcript [32]byte, sequence uint64) []byte {
	aad := make([]byte, 40)
	copy(aad[:32], transcript[:])
	binary.BigEndian.PutUint64(aad[32:], sequence)
	return aad
}

// Seal returns one ordered encrypted frame: uint64 sequence || AES-GCM data.
func (c *Channel) Seal(plaintext []byte) ([]byte, error) {
	if c == nil || c.send == nil { return nil, errors.New("WQPU transport channel is unavailable") }
	if len(plaintext) > MaxPlaintextBytes { return nil, errors.New("WQPU transport frame exceeds plaintext limit") }
	c.sendMu.Lock()
	defer c.sendMu.Unlock()
	if c.sendSeq == 0 || c.sendSeq == math.MaxUint64 { return nil, errors.New("WQPU transport send sequence exhausted") }
	seq := c.sendSeq
	ciphertext := c.send.Seal(nil, frameNonce(c.sendPrefix, seq), plaintext, frameAAD(c.transcript, seq))
	out := make([]byte, 8+len(ciphertext))
	binary.BigEndian.PutUint64(out[:8], seq)
	copy(out[8:], ciphertext)
	c.sendSeq++
	return out, nil
}

// Open accepts strictly ordered frames. Stream transports preserve ordering;
// rejecting skips/replays keeps sequence numbers simple and fail-closed.
func (c *Channel) Open(frame []byte) ([]byte, error) {
	if c == nil || c.recv == nil { return nil, errors.New("WQPU transport channel is unavailable") }
	if len(frame) < 8+c.recv.Overhead() || len(frame) > 8+MaxPlaintextBytes+c.recv.Overhead() { return nil, errors.New("invalid WQPU transport frame length") }
	c.recvMu.Lock()
	defer c.recvMu.Unlock()
	seq := binary.BigEndian.Uint64(frame[:8])
	if seq != c.recvSeq { return nil, errors.New("replayed or out-of-order WQPU transport frame") }
	plaintext, err := c.recv.Open(nil, frameNonce(c.recvPrefix, seq), frame[8:], frameAAD(c.transcript, seq))
	if err != nil { return nil, errors.New("WQPU transport frame authentication failed") }
	if c.recvSeq == math.MaxUint64 { return nil, errors.New("WQPU transport receive sequence exhausted") }
	c.recvSeq++
	return plaintext, nil
}
