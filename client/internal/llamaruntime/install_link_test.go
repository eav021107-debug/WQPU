package llamaruntime

import (
	"archive/tar"
	"os"
	"path/filepath"
	"testing"
)

func TestTarMaterializesSafeInternalSymlinkAsRegularFile(t *testing.T) {
	archive := tarFixture(t, []tar.Header{
		{Name: "runtime/lib/libggml.so.1", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "runtime/lib/libggml.so", Mode: 0o777, Typeflag: tar.TypeSymlink, Linkname: "libggml.so.1"},
	}, [][]byte{[]byte("verified-library"), nil})
	root := t.TempDir()
	if err := extractTarGz(writeFixture(t, archive), root); err != nil { t.Fatal(err) }
	alias := filepath.Join(root, "runtime", "lib", "libggml.so")
	info, err := os.Lstat(alias)
	if err != nil { t.Fatal(err) }
	if !info.Mode().IsRegular() || info.Mode()&os.ModeSymlink != 0 { t.Fatalf("materialized mode=%v", info.Mode()) }
	body, err := os.ReadFile(alias)
	if err != nil { t.Fatal(err) }
	if string(body) != "verified-library" { t.Fatalf("materialized body=%q", body) }
}

func TestTarMaterializesSafeHardlinkAsRegularFile(t *testing.T) {
	archive := tarFixture(t, []tar.Header{
		{Name: "runtime/bin/tool.real", Mode: 0o755, Typeflag: tar.TypeReg},
		{Name: "runtime/bin/tool", Mode: 0o755, Typeflag: tar.TypeLink, Linkname: "runtime/bin/tool.real"},
	}, [][]byte{[]byte("verified-tool"), nil})
	root := t.TempDir()
	if err := extractTarGz(writeFixture(t, archive), root); err != nil { t.Fatal(err) }
	alias := filepath.Join(root, "runtime", "bin", "tool")
	info, err := os.Lstat(alias)
	if err != nil { t.Fatal(err) }
	if !info.Mode().IsRegular() { t.Fatalf("hardlink materialized mode=%v", info.Mode()) }
	body, err := os.ReadFile(alias)
	if err != nil { t.Fatal(err) }
	if string(body) != "verified-tool" { t.Fatalf("materialized body=%q", body) }
}

func TestTarRejectsMissingAndCyclicInternalLinks(t *testing.T) {
	missing := tarFixture(t, []tar.Header{{Name: "runtime/lib/missing", Typeflag: tar.TypeSymlink, Linkname: "does-not-exist"}}, [][]byte{nil})
	if err := extractTarGz(writeFixture(t, missing), t.TempDir()); err == nil { t.Fatal("missing link target should fail") }

	cycle := tarFixture(t, []tar.Header{
		{Name: "runtime/lib/a", Typeflag: tar.TypeSymlink, Linkname: "b"},
		{Name: "runtime/lib/b", Typeflag: tar.TypeSymlink, Linkname: "a"},
	}, [][]byte{nil, nil})
	if err := extractTarGz(writeFixture(t, cycle), t.TempDir()); err == nil { t.Fatal("cyclic link targets should fail") }
}
