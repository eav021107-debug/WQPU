package llamaruntime

import (
	"errors"
	"fmt"
	"net"
	"path/filepath"
	"strconv"
	"strings"
)

const (
	PinnedTag       = "b10453"
	RPCServerBinary = "ggml-rpc-server"
	LlamaServerBinary = "llama-server"
	LlamaCLIBinary  = "llama-cli"
	DefaultRPCPort  = 50052
)

type ArchiveKind string

const (
	TarGz ArchiveKind = "tar.gz"
	Zip   ArchiveKind = "zip"
)

type Asset struct {
	Name   string
	SHA256 string
	Kind   ArchiveKind
}

var cpuAssets = map[string]Asset{
	"darwin/arm64": {
		Name: "llama-b10453-bin-macos-arm64.tar.gz",
		SHA256: "f1531b1c520f8b473d83352c5eec2f4f43bd0a54f9ca1366a6f202211cfbc098",
		Kind: TarGz,
	},
	"darwin/amd64": {
		Name: "llama-b10453-bin-macos-x64.tar.gz",
		SHA256: "ac13f6f6c90c193765921bf52dd5ecf2a9d506ee9c3eadd2d6fd49ca7a5de25d",
		Kind: TarGz,
	},
	"linux/arm64": {
		Name: "llama-b10453-bin-ubuntu-arm64.tar.gz",
		SHA256: "b164e72dfb69c711275178e0d0fae54748042f039e4fe7386f1c0ea7019c109c",
		Kind: TarGz,
	},
	"linux/amd64": {
		Name: "llama-b10453-bin-ubuntu-x64.tar.gz",
		SHA256: "550eb155a09c3051c7add5becf6d0badc3a4c33416807985963036b27b859fb4",
		Kind: TarGz,
	},
	"windows/arm64": {
		Name: "llama-b10453-bin-win-cpu-arm64.zip",
		SHA256: "a8b984d478700777d4671cf33eccfddae42c1fd871e78efd43fee090131eec1f",
		Kind: Zip,
	},
	"windows/amd64": {
		Name: "llama-b10453-bin-win-cpu-x64.zip",
		SHA256: "70c07211d0027305f0be09cd755d79641ebb0bb646590ff3d498c66b22df29b0",
		Kind: Zip,
	},
}

func CPUAsset(goos, goarch string) (Asset, error) {
	asset, ok := cpuAssets[goos+"/"+goarch]
	if !ok {
		return Asset{}, fmt.Errorf("unsupported WQPU llama.cpp platform %s/%s", goos, goarch)
	}
	return asset, nil
}

func (a Asset) URL() string {
	if a.Name == "" { return "" }
	return "https://github.com/ggml-org/llama.cpp/releases/download/" + PinnedTag + "/" + a.Name
}

func executableName(base, goos string) string {
	if goos == "windows" { return base + ".exe" }
	return base
}

func BinaryNames(goos string) (rpc, server, cli string) {
	return executableName(RPCServerBinary, goos), executableName(LlamaServerBinary, goos), executableName(LlamaCLIBinary, goos)
}

func validatePort(port int) error {
	if port <= 0 || port > 65535 { return errors.New("WQPU runtime port is outside 1..65535") }
	return nil
}

func validateDevice(device string) error {
	if device == "" || len(device) > 128 || strings.ContainsAny(device, "\x00\r\n,") {
		return errors.New("invalid llama.cpp device name")
	}
	return nil
}

// RPCServerArgs always binds upstream's insecure proof-of-concept RPC server to
// loopback. WQPU never publishes this port; remote access goes through SecureStream.
func RPCServerArgs(port, threads int, devices []string, cache bool) ([]string, error) {
	if err := validatePort(port); err != nil { return nil, err }
	if threads < 0 { return nil, errors.New("llama.cpp RPC thread count cannot be negative") }
	args := []string{"--host", "127.0.0.1", "--port", strconv.Itoa(port)}
	if threads > 0 { args = append(args, "--threads", strconv.Itoa(threads)) }
	for _, device := range devices {
		if err := validateDevice(device); err != nil { return nil, err }
		args = append(args, "--device", device)
	}
	if cache { args = append(args, "--cache") }
	return args, nil
}

func validateLoopbackRPC(endpoint string) error {
	host, portText, err := net.SplitHostPort(endpoint)
	if err != nil { return errors.New("llama.cpp RPC endpoint must be host:port") }
	if strings.Contains(host, "%") { return errors.New("scoped loopback RPC endpoint is not allowed") }
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() { return errors.New("llama.cpp RPC endpoint must be literal loopback") }
	port, err := strconv.ParseUint(portText, 10, 16)
	if err != nil || port == 0 { return errors.New("invalid llama.cpp RPC endpoint port") }
	return nil
}

func baseServerArgs(apiPort int, rpcEndpoints []string) ([]string, error) {
	if err := validatePort(apiPort); err != nil { return nil, err }
	if len(rpcEndpoints) == 0 { return nil, errors.New("at least one WQPU RPC forwarder is required") }
	seen := make(map[string]struct{}, len(rpcEndpoints))
	for _, endpoint := range rpcEndpoints {
		if err := validateLoopbackRPC(endpoint); err != nil { return nil, err }
		if _, ok := seen[endpoint]; ok { return nil, errors.New("duplicate WQPU RPC forwarder endpoint") }
		seen[endpoint] = struct{}{}
	}
	return []string{
		"--host", "127.0.0.1",
		"--port", strconv.Itoa(apiPort),
		"--rpc", strings.Join(rpcEndpoints, ","),
		"--fit", "on",
	}, nil
}

func ServerArgsForModel(apiPort int, rpcEndpoints []string, modelPath string) ([]string, error) {
	if modelPath == "" || strings.ContainsRune(modelPath, '\x00') { return nil, errors.New("valid model path is required") }
	clean := filepath.Clean(modelPath)
	args, err := baseServerArgs(apiPort, rpcEndpoints)
	if err != nil { return nil, err }
	return append(args, "--model", clean), nil
}

func ServerArgsForHFRepo(apiPort int, rpcEndpoints []string, repo string) ([]string, error) {
	if repo == "" || len(repo) > 256 || strings.ContainsAny(repo, "\x00\r\n ") { return nil, errors.New("valid Hugging Face repo is required") }
	if strings.Count(repo, "/") != 1 { return nil, errors.New("Hugging Face repo must be owner/name") }
	args, err := baseServerArgs(apiPort, rpcEndpoints)
	if err != nil { return nil, err }
	return append(args, "--hf-repo", repo), nil
}
