package llamaruntime

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func tarFixture(t *testing.T, entries []tar.Header, bodies [][]byte) []byte {
	t.Helper()
	if len(entries) != len(bodies) { t.Fatal("fixture entry/body mismatch") }
	var compressed bytes.Buffer
	gz := gzip.NewWriter(&compressed)
	tw := tar.NewWriter(gz)
	for index := range entries {
		header := entries[index]
		body := bodies[index]
		if header.Typeflag == 0 { header.Typeflag = tar.TypeReg }
		regular := header.Typeflag == tar.TypeReg || header.Typeflag == tar.TypeRegA
		if regular { header.Size = int64(len(body)) }
		if err := tw.WriteHeader(&header); err != nil { t.Fatal(err) }
		if regular && len(body) > 0 {
			if _, err := tw.Write(body); err != nil { t.Fatal(err) }
		}
	}
	if err := tw.Close(); err != nil { t.Fatal(err) }
	if err := gz.Close(); err != nil { t.Fatal(err) }
	return compressed.Bytes()
}

func writeFixture(t *testing.T, data []byte) string {
	t.Helper()
	name := filepath.Join(t.TempDir(), "fixture.archive")
	if err := os.WriteFile(name, data, 0o600); err != nil { t.Fatal(err) }
	return name
}

func TestTarExtractionFindsCompleteRuntime(t *testing.T) {
	archive := tarFixture(t, []tar.Header{
		{Name: "llama/bin/ggml-rpc-server", Mode: 0o755},
		{Name: "llama/bin/llama-server", Mode: 0o755},
		{Name: "llama/bin/llama-cli", Mode: 0o755},
		{Name: "llama/lib/libggml.so", Mode: 0o644},
	}, [][]byte{[]byte("rpc"), []byte("server"), []byte("cli"), []byte("lib")})
	root := t.TempDir()
	if err := extractTarGz(writeFixture(t, archive), root); err != nil { t.Fatal(err) }
	runtime, err := inspectRuntime(root, "linux")
	if err != nil { t.Fatal(err) }
	if runtime.Tag != PinnedTag || filepath.Base(runtime.RPCServer) != "ggml-rpc-server" || filepath.Base(runtime.LlamaServer) != "llama-server" || filepath.Base(runtime.LlamaCLI) != "llama-cli" {
		t.Fatalf("runtime=%+v", runtime)
	}
}

func TestTarRejectsTraversalAndLinks(t *testing.T) {
	cases := []tar.Header{
		{Name: "../escape", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "..\\escape", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "/absolute", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "C:/escape", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "runtime/link", Mode: 0o777, Typeflag: tar.TypeSymlink, Linkname: "../../escape"},
		{Name: "runtime/hard", Mode: 0o777, Typeflag: tar.TypeLink, Linkname: "../../escape"},
	}
	for _, header := range cases {
		header := header
		t.Run(strings.ReplaceAll(header.Name, "/", "_"), func(t *testing.T) {
			archive := tarFixture(t, []tar.Header{header}, [][]byte{[]byte("x")})
			if err := extractTarGz(writeFixture(t, archive), t.TempDir()); err == nil {
				t.Fatalf("unsafe tar entry accepted: %+v", header)
			}
		})
	}
}

func zipFixture(t *testing.T, name string, mode os.FileMode, body []byte) []byte {
	t.Helper()
	var data bytes.Buffer
	zw := zip.NewWriter(&data)
	header := &zip.FileHeader{Name: name, Method: zip.Store}
	header.SetMode(mode)
	writer, err := zw.CreateHeader(header)
	if err != nil { t.Fatal(err) }
	if _, err := writer.Write(body); err != nil { t.Fatal(err) }
	if err := zw.Close(); err != nil { t.Fatal(err) }
	return data.Bytes()
}

func TestZipRejectsTraversalAndSymlink(t *testing.T) {
	for _, name := range []string{"../escape.exe", "..\\escape.exe", "/escape.exe", "C:/escape.exe"} {
		if err := extractZip(writeFixture(t, zipFixture(t, name, 0o755, []byte("x"))), t.TempDir()); err == nil {
			t.Fatalf("unsafe zip path accepted: %q", name)
		}
	}
	if err := extractZip(writeFixture(t, zipFixture(t, "runtime/link", os.ModeSymlink|0o777, []byte("../../escape"))), t.TempDir()); err == nil {
		t.Fatal("zip symlink should be rejected")
	}
}

func TestVerifySHA256RejectsMismatch(t *testing.T) {
	name := filepath.Join(t.TempDir(), "archive")
	if err := os.WriteFile(name, []byte("verified"), 0o600); err != nil { t.Fatal(err) }
	sum := sha256.Sum256([]byte("verified"))
	if err := verifySHA256(name, hex.EncodeToString(sum[:])); err != nil { t.Fatal(err) }
	if err := verifySHA256(name, strings.Repeat("0", 64)); err == nil { t.Fatal("hash mismatch should fail") }
}

func TestDownloadVerifiesBeforeExtraction(t *testing.T) {
	payload := []byte("fake pinned archive")
	sum := sha256.Sum256(payload)
	asset := Asset{Name: "fixture.tar.gz", SHA256: hex.EncodeToString(sum[:]), Kind: TarGz}
	client := &http.Client{Transport: roundTripFunc(func(request *http.Request) (*http.Response, error) {
		if !strings.Contains(request.URL.Path, "/releases/download/"+PinnedTag+"/") { t.Fatalf("unversioned URL: %s", request.URL) }
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(bytes.NewReader(payload)), Header: make(http.Header)}, nil
	})}
	target := filepath.Join(t.TempDir(), "download")
	if err := (Installer{Client: client}).download(context.Background(), asset, target); err != nil { t.Fatal(err) }
	if got, err := os.ReadFile(target); err != nil || !bytes.Equal(got, payload) { t.Fatalf("download=%q err=%v", got, err) }

	bad := asset
	bad.SHA256 = strings.Repeat("0", 64)
	if err := (Installer{Client: client}).download(context.Background(), bad, filepath.Join(t.TempDir(), "bad")); err == nil {
		t.Fatal("unverified download should fail")
	}
}

func TestInspectRuntimeRejectsMissingAndDuplicateBinaries(t *testing.T) {
	missing := t.TempDir()
	if err := os.WriteFile(filepath.Join(missing, "ggml-rpc-server"), []byte("rpc"), 0o755); err != nil { t.Fatal(err) }
	if _, err := inspectRuntime(missing, "linux"); err == nil { t.Fatal("incomplete runtime should fail") }

	duplicate := t.TempDir()
	for _, dir := range []string{"a", "b"} {
		if err := os.MkdirAll(filepath.Join(duplicate, dir), 0o755); err != nil { t.Fatal(err) }
		if err := os.WriteFile(filepath.Join(duplicate, dir, "ggml-rpc-server"), []byte("rpc"), 0o755); err != nil { t.Fatal(err) }
	}
	if err := os.WriteFile(filepath.Join(duplicate, "llama-server"), []byte("server"), 0o755); err != nil { t.Fatal(err) }
	if err := os.WriteFile(filepath.Join(duplicate, "llama-cli"), []byte("cli"), 0o755); err != nil { t.Fatal(err) }
	if _, err := inspectRuntime(duplicate, "linux"); err == nil { t.Fatal("duplicate required binary should fail") }
}
