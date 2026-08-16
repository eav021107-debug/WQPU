package transport

import (
	"encoding/binary"
	"errors"
	"io"
	"sync"
)

const (
	maxHelloWireBytes = 4 + 1 + 1 + MaxChainIDBytes + 32 + 20 + 32 + 32 + helloSignatureBytes
	maxEncryptedFrameBytes = 8 + MaxPlaintextBytes + 16 // sequence + AES-GCM ciphertext/tag
)

func writeAll(w io.Writer, data []byte) error {
	for len(data) > 0 {
		n, err := w.Write(data)
		if err != nil { return err }
		if n <= 0 || n > len(data) { return io.ErrShortWrite }
		data = data[n:]
	}
	return nil
}

func writeHello(w io.Writer, data []byte) error {
	if len(data) == 0 || len(data) > maxHelloWireBytes || len(data) > int(^uint16(0)) {
		return errors.New("invalid WQPU handshake wire length")
	}
	var prefix [2]byte
	binary.BigEndian.PutUint16(prefix[:], uint16(len(data)))
	if err := writeAll(w, prefix[:]); err != nil { return err }
	return writeAll(w, data)
}

func readHello(r io.Reader) (Hello, error) {
	var prefix [2]byte
	if _, err := io.ReadFull(r, prefix[:]); err != nil { return Hello{}, err }
	length := int(binary.BigEndian.Uint16(prefix[:]))
	if length == 0 || length > maxHelloWireBytes { return Hello{}, errors.New("invalid WQPU handshake wire length") }
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil { return Hello{}, err }
	return ParseHello(payload)
}

// SecureStream is an ordered authenticated byte stream. The underlying stream
// may be TCP, QUIC, libp2p, a relay stream, or an in-memory pipe; WQPU's
// cryptographic peer identity is independent of that carrier.
type SecureStream struct {
	raw     io.ReadWriteCloser
	channel *Channel

	writeMu sync.Mutex
	readMu  sync.Mutex
	pending []byte
}

// Initiate performs initiator-first handshake. expectedRemoteSession must be
// obtained from verified WQPU chain registry state for expectedRemotePeerID.
func Initiate(raw io.ReadWriteCloser, signer Signer, chainID string, localPeerID, expectedRemotePeerID [32]byte, expectedRemoteSession string) (*SecureStream, error) {
	if raw == nil { return nil, errors.New("WQPU carrier stream is required") }
	handshake, err := NewHandshake(RoleInitiator, signer, chainID, localPeerID)
	if err != nil { return nil, err }
	if err := writeHello(raw, handshake.Bytes()); err != nil { return nil, err }
	remote, err := readHello(raw)
	if err != nil { return nil, err }
	channel, err := handshake.Establish(remote, expectedRemotePeerID, expectedRemoteSession)
	if err != nil { return nil, err }
	return &SecureStream{raw: raw, channel: channel}, nil
}

// Accept verifies the initiator before sending the responder hello. This avoids
// giving unauthenticated peers an encrypted channel or any application bytes.
func Accept(raw io.ReadWriteCloser, signer Signer, chainID string, localPeerID, expectedRemotePeerID [32]byte, expectedRemoteSession string) (*SecureStream, error) {
	if raw == nil { return nil, errors.New("WQPU carrier stream is required") }
	remote, err := readHello(raw)
	if err != nil { return nil, err }
	handshake, err := NewHandshake(RoleResponder, signer, chainID, localPeerID)
	if err != nil { return nil, err }
	channel, err := handshake.Establish(remote, expectedRemotePeerID, expectedRemoteSession)
	if err != nil { return nil, err }
	if err := writeHello(raw, handshake.Bytes()); err != nil { return nil, err }
	return &SecureStream{raw: raw, channel: channel}, nil
}

func (s *SecureStream) writeFrame(frame []byte) error {
	if len(frame) == 0 || len(frame) > maxEncryptedFrameBytes {
		return errors.New("invalid WQPU encrypted frame length")
	}
	var prefix [4]byte
	binary.BigEndian.PutUint32(prefix[:], uint32(len(frame)))
	if err := writeAll(s.raw, prefix[:]); err != nil { return err }
	return writeAll(s.raw, frame)
}

func (s *SecureStream) readFrame() ([]byte, error) {
	var prefix [4]byte
	if _, err := io.ReadFull(s.raw, prefix[:]); err != nil { return nil, err }
	length := uint64(binary.BigEndian.Uint32(prefix[:]))
	if length == 0 || length > maxEncryptedFrameBytes {
		return nil, errors.New("invalid WQPU encrypted frame wire length")
	}
	frame := make([]byte, int(length))
	if _, err := io.ReadFull(s.raw, frame); err != nil { return nil, err }
	return s.channel.Open(frame)
}

// Write preserves stream semantics while chunking large llama/RPC writes into
// bounded authenticated frames. Concurrent writers cannot interleave frames.
func (s *SecureStream) Write(p []byte) (int, error) {
	if s == nil || s.raw == nil || s.channel == nil { return 0, errors.New("WQPU secure stream is unavailable") }
	if len(p) == 0 { return 0, nil }
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	written := 0
	for written < len(p) {
		end := written + MaxPlaintextBytes
		if end > len(p) { end = len(p) }
		frame, err := s.channel.Seal(p[written:end])
		if err != nil { return written, err }
		if err := s.writeFrame(frame); err != nil { return written, err }
		written = end
	}
	return written, nil
}

// Read reassembles encrypted frames into ordinary byte-stream semantics.
func (s *SecureStream) Read(p []byte) (int, error) {
	if s == nil || s.raw == nil || s.channel == nil { return 0, errors.New("WQPU secure stream is unavailable") }
	if len(p) == 0 { return 0, nil }
	s.readMu.Lock()
	defer s.readMu.Unlock()
	if len(s.pending) == 0 {
		plaintext, err := s.readFrame()
		if err != nil { return 0, err }
		s.pending = plaintext
	}
	n := copy(p, s.pending)
	s.pending = s.pending[n:]
	return n, nil
}

func (s *SecureStream) Close() error {
	if s == nil || s.raw == nil { return nil }
	return s.raw.Close()
}
