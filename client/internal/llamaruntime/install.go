package llamaruntime

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"strings"
)

const (
	MaxArchiveBytes   int64 = 1 << 30 // 1 GiB compressed download ceiling
	MaxExtractedBytes int64 = 4 << 30 // 4 GiB expanded runtime ceiling
	MaxArchiveFiles         = 20_000
)

type Runtime struct {
	Root        string
	RPCServer   string
	LlamaServer string
	LlamaCLI    string
	Tag         string
}

type Installer struct {
	Client *http.Client
}

func (i Installer) client() *http.Client {
	if i.Client != nil { return i.Client }
	return http.DefaultClient
}

func runtimeDir(baseDir, goos, goarch string) string {
	return filepath.Join(baseDir, "llama.cpp", PinnedTag, goos+"-"+goarch)
}

func archiveRelative(name string) (string, error) {
	if name == "" || strings.ContainsRune(name, '\x00') {
		return "", errors.New("invalid empty archive path")
	}
	// Archive formats use slash paths. Treat backslash as a separator too so a
	// malicious Windows-style traversal cannot escape when extracted on Unix.
	normalized := strings.ReplaceAll(name, "\\", "/")
	clean := path.Clean(normalized)
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || path.IsAbs(clean) {
		return "", errors.New("archive path escapes WQPU runtime directory")
	}
	// Reject drive-like or UNC-ish names independent of the host OS.
	first := clean
	if slash := strings.IndexByte(first, '/'); slash >= 0 { first = first[:slash] }
	if strings.Contains(first, ":") || strings.HasPrefix(normalized, "//") {
		return "", errors.New("archive path contains an unsafe volume prefix")
	}
	return filepath.FromSlash(clean), nil
}

func destination(root, archiveName string) (string, error) {
	rel, err := archiveRelative(archiveName)
	if err != nil { return "", err }
	rootAbs, err := filepath.Abs(root)
	if err != nil { return "", err }
	dstAbs, err := filepath.Abs(filepath.Join(rootAbs, rel))
	if err != nil { return "", err }
	prefix := rootAbs + string(os.PathSeparator)
	if dstAbs != rootAbs && !strings.HasPrefix(dstAbs, prefix) {
		return "", errors.New("archive destination escapes WQPU runtime directory")
	}
	return dstAbs, nil
}

func copyBounded(dst io.Writer, src io.Reader, remaining *int64) error {
	if remaining == nil || *remaining < 0 { return errors.New("invalid extraction budget") }
	limited := &io.LimitedReader{R: src, N: *remaining + 1}
	n, err := io.Copy(dst, limited)
	if err != nil { return err }
	if n > *remaining { return errors.New("llama.cpp archive exceeds extracted size limit") }
	*remaining -= n
	return nil
}

type deferredTarLink struct {
	destination string
	target      string
}

func tarLinkTarget(root, headerName, linkName string, hardLink bool) (string, error) {
	if linkName == "" || strings.ContainsRune(linkName, '\x00') {
		return "", errors.New("empty llama.cpp archive link target")
	}
	source := path.Clean(strings.ReplaceAll(headerName, "\\", "/"))
	link := strings.ReplaceAll(linkName, "\\", "/")
	if path.IsAbs(link) || strings.HasPrefix(link, "//") {
		return "", errors.New("absolute llama.cpp archive link target")
	}
	first := link
	if slash := strings.IndexByte(first, '/'); slash >= 0 { first = first[:slash] }
	if strings.Contains(first, ":") {
		return "", errors.New("unsafe llama.cpp archive link volume")
	}
	var targetName string
	if hardLink {
		targetName = path.Clean(link)
	} else {
		targetName = path.Clean(path.Join(path.Dir(source), link))
	}
	if targetName == "." || targetName == ".." || strings.HasPrefix(targetName, "../") || path.IsAbs(targetName) {
		return "", errors.New("llama.cpp archive link escapes runtime directory")
	}
	return destination(root, targetName)
}

func materializeTarLinks(links []deferredTarLink, remaining *int64) error {
	pending := append([]deferredTarLink(nil), links...)
	for len(pending) > 0 {
		progress := false
		next := pending[:0]
		for _, link := range pending {
			info, err := os.Stat(link.target)
			if errors.Is(err, os.ErrNotExist) {
				next = append(next, link)
				continue
			}
			if err != nil { return err }
			if !info.Mode().IsRegular() {
				return errors.New("llama.cpp archive link target is not a regular file")
			}
			if _, err := os.Lstat(link.destination); err == nil {
				return errors.New("duplicate llama.cpp archive link destination")
			} else if !errors.Is(err, os.ErrNotExist) {
				return err
			}
			if err := os.MkdirAll(filepath.Dir(link.destination), 0o755); err != nil { return err }
			in, err := os.Open(link.target)
			if err != nil { return err }
			mode := os.FileMode(0o644)
			if info.Mode()&0o111 != 0 { mode = 0o755 }
			out, err := os.OpenFile(link.destination, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
			if err != nil { _ = in.Close(); return err }
			copyErr := copyBounded(out, in, remaining)
			inCloseErr := in.Close()
			outCloseErr := out.Close()
			if copyErr != nil { return copyErr }
			if inCloseErr != nil { return inCloseErr }
			if outCloseErr != nil { return outCloseErr }
			progress = true
		}
		if !progress {
			return errors.New("llama.cpp archive contains a missing or cyclic internal link")
		}
		pending = append([]deferredTarLink(nil), next...)
	}
	return nil
}

func extractTarGz(archivePath, root string) error {
	file, err := os.Open(archivePath)
	if err != nil { return err }
	defer file.Close()
	gz, err := gzip.NewReader(file)
	if err != nil { return err }
	defer gz.Close()
	tr := tar.NewReader(gz)
	remaining := MaxExtractedBytes
	files := 0
	links := make([]deferredTarLink, 0)
	seenLinks := make(map[string]struct{})
	for {
		header, err := tr.Next()
		if errors.Is(err, io.EOF) { break }
		if err != nil { return err }
		files++
		if files > MaxArchiveFiles { return errors.New("llama.cpp archive contains too many entries") }
		dst, err := destination(root, header.Name)
		if err != nil { return err }
		switch header.Typeflag {
		case tar.TypeDir:
			if err := os.MkdirAll(dst, 0o755); err != nil { return err }
		case tar.TypeReg, tar.TypeRegA:
			if header.Size < 0 || header.Size > remaining { return errors.New("llama.cpp archive exceeds extracted size limit") }
			if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil { return err }
			mode := os.FileMode(0o644)
			if header.FileInfo().Mode()&0o111 != 0 { mode = 0o755 }
			out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_EXCL, mode)
			if err != nil { return err }
			copyErr := copyBounded(out, tr, &remaining)
			closeErr := out.Close()
			if copyErr != nil { return copyErr }
			if closeErr != nil { return closeErr }
		case tar.TypeSymlink, tar.TypeLink:
			if _, duplicate := seenLinks[dst]; duplicate { return errors.New("duplicate llama.cpp archive link") }
			if _, err := os.Lstat(dst); err == nil {
				return errors.New("llama.cpp archive link collides with existing entry")
			} else if !errors.Is(err, os.ErrNotExist) { return err }
			target, err := tarLinkTarget(root, header.Name, header.Linkname, header.Typeflag == tar.TypeLink)
			if err != nil { return err }
			if target == dst { return errors.New("self-referential llama.cpp archive link") }
			seenLinks[dst] = struct{}{}
			links = append(links, deferredTarLink{destination: dst, target: target})
		default:
			return fmt.Errorf("unsafe llama.cpp tar entry type %d", header.Typeflag)
		}
	}
	return materializeTarLinks(links, &remaining)
}

func extractZip(archivePath, root string) error {
	zr, err := zip.OpenReader(archivePath)
	if err != nil { return err }
	defer zr.Close()
	if len(zr.File) > MaxArchiveFiles { return errors.New("llama.cpp archive contains too many entries") }
	remaining := MaxExtractedBytes
	for _, entry := range zr.File {
		dst, err := destination(root, entry.Name)
		if err != nil { return err }
		mode := entry.Mode()
		if mode&os.ModeSymlink != 0 || !mode.IsRegular() && !entry.FileInfo().IsDir() {
			return errors.New("unsafe llama.cpp zip entry type")
		}
		if entry.FileInfo().IsDir() {
			if err := os.MkdirAll(dst, 0o755); err != nil { return err }
			continue
		}
		if entry.UncompressedSize64 > uint64(remaining) { return errors.New("llama.cpp archive exceeds extracted size limit") }
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil { return err }
		in, err := entry.Open()
		if err != nil { return err }
		fileMode := os.FileMode(0o644)
		if mode&0o111 != 0 { fileMode = 0o755 }
		out, err := os.OpenFile(dst, os.O_WRONLY|os.O_CREATE|os.O_EXCL, fileMode)
		if err != nil { _ = in.Close(); return err }
		copyErr := copyBounded(out, in, &remaining)
		inCloseErr := in.Close()
		outCloseErr := out.Close()
		if copyErr != nil { return copyErr }
		if inCloseErr != nil { return inCloseErr }
		if outCloseErr != nil { return outCloseErr }
	}
	return nil
}

func findRequiredBinary(root, name string) (string, error) {
	var found string
	err := filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil { return err }
		if d.Type()&os.ModeSymlink != 0 { return errors.New("symlinks are not allowed in installed llama.cpp runtime") }
		if d.IsDir() || d.Name() != name { return nil }
		info, err := d.Info()
		if err != nil { return err }
		if !info.Mode().IsRegular() { return errors.New("llama.cpp binary is not a regular file") }
		if found != "" { return fmt.Errorf("duplicate llama.cpp binary %s", name) }
		found = p
		return nil
	})
	if err != nil { return "", err }
	if found == "" { return "", fmt.Errorf("required llama.cpp binary %s not found", name) }
	return found, nil
}

func inspectRuntime(root, goos string) (Runtime, error) {
	rpcName, serverName, cliName := BinaryNames(goos)
	rpc, err := findRequiredBinary(root, rpcName)
	if err != nil { return Runtime{}, err }
	server, err := findRequiredBinary(root, serverName)
	if err != nil { return Runtime{}, err }
	cli, err := findRequiredBinary(root, cliName)
	if err != nil { return Runtime{}, err }
	return Runtime{Root: root, RPCServer: rpc, LlamaServer: server, LlamaCLI: cli, Tag: PinnedTag}, nil
}

func verifySHA256(pathName, expected string) error {
	if len(expected) != sha256.Size*2 { return errors.New("invalid expected llama.cpp SHA-256") }
	file, err := os.Open(pathName)
	if err != nil { return err }
	defer file.Close()
	hash := sha256.New()
	if _, err := io.Copy(hash, file); err != nil { return err }
	actual := hex.EncodeToString(hash.Sum(nil))
	if !strings.EqualFold(actual, expected) {
		return fmt.Errorf("llama.cpp archive SHA-256 mismatch: got %s", actual)
	}
	return nil
}

func (i Installer) download(ctx context.Context, asset Asset, target string) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, asset.URL(), nil)
	if err != nil { return err }
	response, err := i.client().Do(request)
	if err != nil { return err }
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("llama.cpp download returned HTTP %d", response.StatusCode)
	}
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil { return err }
	limited := &io.LimitedReader{R: response.Body, N: MaxArchiveBytes + 1}
	n, copyErr := io.Copy(out, limited)
	closeErr := out.Close()
	if copyErr != nil { return copyErr }
	if closeErr != nil { return closeErr }
	if n > MaxArchiveBytes { return errors.New("llama.cpp download exceeds archive size limit") }
	return verifySHA256(target, asset.SHA256)
}

func extractAsset(asset Asset, archivePath, root string) error {
	switch asset.Kind {
	case TarGz:
		return extractTarGz(archivePath, root)
	case Zip:
		return extractZip(archivePath, root)
	default:
		return errors.New("unsupported llama.cpp archive kind")
	}
}

// InstallCPU downloads the exact pinned official llama.cpp archive, verifies its
// SHA-256 before extraction, rejects archive escapes and unsafe special files,
// materializes verified internal tar links as ordinary files, and atomically
// publishes a complete runtime directory. Existing complete installs are reused
// without a network request.
func (i Installer) InstallCPU(ctx context.Context, baseDir, goos, goarch string) (Runtime, error) {
	if baseDir == "" || strings.ContainsRune(baseDir, '\x00') { return Runtime{}, errors.New("valid WQPU runtime base directory is required") }
	asset, err := CPUAsset(goos, goarch)
	if err != nil { return Runtime{}, err }
	finalDir := runtimeDir(baseDir, goos, goarch)
	if runtime, err := inspectRuntime(finalDir, goos); err == nil { return runtime, nil }

	parent := filepath.Dir(finalDir)
	if err := os.MkdirAll(parent, 0o755); err != nil { return Runtime{}, err }
	stage, err := os.MkdirTemp(parent, "."+PinnedTag+"-install-")
	if err != nil { return Runtime{}, err }
	defer os.RemoveAll(stage)
	archivePath := filepath.Join(stage, "runtime.archive")
	if err := i.download(ctx, asset, archivePath); err != nil { return Runtime{}, err }
	extractRoot := filepath.Join(stage, "root")
	if err := os.Mkdir(extractRoot, 0o755); err != nil { return Runtime{}, err }
	if err := extractAsset(asset, archivePath, extractRoot); err != nil { return Runtime{}, err }
	if _, err := inspectRuntime(extractRoot, goos); err != nil { return Runtime{}, err }

	if err := os.Rename(extractRoot, finalDir); err != nil {
		// Another process may have won the same install race. Reuse it only if it
		// is a complete, validated runtime; otherwise preserve the original error.
		if runtime, inspectErr := inspectRuntime(finalDir, goos); inspectErr == nil { return runtime, nil }
		return Runtime{}, err
	}
	return inspectRuntime(finalDir, goos)
}
