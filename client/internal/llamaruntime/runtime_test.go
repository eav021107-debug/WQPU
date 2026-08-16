package llamaruntime

import (
	"reflect"
	"strings"
	"testing"
)

func TestPinnedCPUAssets(t *testing.T) {
	tests := []struct {
		os, arch, name, sha string
		kind ArchiveKind
	}{
		{"darwin", "arm64", "llama-b10453-bin-macos-arm64.tar.gz", "f1531b1c520f8b473d83352c5eec2f4f43bd0a54f9ca1366a6f202211cfbc098", TarGz},
		{"darwin", "amd64", "llama-b10453-bin-macos-x64.tar.gz", "ac13f6f6c90c193765921bf52dd5ecf2a9d506ee9c3eadd2d6fd49ca7a5de25d", TarGz},
		{"linux", "arm64", "llama-b10453-bin-ubuntu-arm64.tar.gz", "b164e72dfb69c711275178e0d0fae54748042f039e4fe7386f1c0ea7019c109c", TarGz},
		{"linux", "amd64", "llama-b10453-bin-ubuntu-x64.tar.gz", "550eb155a09c3051c7add5becf6d0badc3a4c33416807985963036b27b859fb4", TarGz},
		{"windows", "arm64", "llama-b10453-bin-win-cpu-arm64.zip", "a8b984d478700777d4671cf33eccfddae42c1fd871e78efd43fee090131eec1f", Zip},
		{"windows", "amd64", "llama-b10453-bin-win-cpu-x64.zip", "70c07211d0027305f0be09cd755d79641ebb0bb646590ff3d498c66b22df29b0", Zip},
	}
	for _, tt := range tests {
		asset, err := CPUAsset(tt.os, tt.arch)
		if err != nil { t.Fatalf("%s/%s: %v", tt.os, tt.arch, err) }
		if asset.Name != tt.name || asset.SHA256 != tt.sha || asset.Kind != tt.kind { t.Fatalf("%s/%s: %+v", tt.os, tt.arch, asset) }
		if !strings.Contains(asset.URL(), "/releases/download/"+PinnedTag+"/") { t.Fatalf("unpinned URL: %s", asset.URL()) }
	}
	if _, err := CPUAsset("plan9", "amd64"); err == nil { t.Fatal("unsupported platform should fail") }
}

func TestBinaryNamesMatchPinnedLlamaTarget(t *testing.T) {
	rpc, server, cli := BinaryNames("linux")
	if rpc != "ggml-rpc-server" || server != "llama-server" || cli != "llama-cli" { t.Fatalf("linux binaries=%q %q %q", rpc, server, cli) }
	rpc, server, cli = BinaryNames("windows")
	if rpc != "ggml-rpc-server.exe" || server != "llama-server.exe" || cli != "llama-cli.exe" { t.Fatalf("windows binaries=%q %q %q", rpc, server, cli) }
}

func TestRPCServerArgsStayOnLoopback(t *testing.T) {
	args, err := RPCServerArgs(50052, 8, []string{"CUDA0", "CPU"}, true)
	if err != nil { t.Fatal(err) }
	want := []string{"--host", "127.0.0.1", "--port", "50052", "--threads", "8", "--device", "CUDA0", "--device", "CPU", "--cache"}
	if !reflect.DeepEqual(args, want) { t.Fatalf("args=%v", args) }
	if _, err := RPCServerArgs(0, 1, nil, false); err == nil { t.Fatal("zero port should fail") }
	if _, err := RPCServerArgs(50052, 1, []string{"CUDA0,CPU"}, false); err == nil { t.Fatal("ambiguous comma device should fail") }
}

func TestServerArgsAcceptOnlyLocalWQPUForwarders(t *testing.T) {
	args, err := ServerArgsForHFRepo(8080, []string{"127.0.0.1:41001", "[::1]:41002"}, "ggml-org/gemma-3-1b-it-GGUF")
	if err != nil { t.Fatal(err) }
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "--host 127.0.0.1") || !strings.Contains(joined, "--rpc 127.0.0.1:41001,[::1]:41002") || !strings.Contains(joined, "--fit on") || !strings.Contains(joined, "--hf-repo ggml-org/gemma-3-1b-it-GGUF") {
		t.Fatalf("args=%v", args)
	}
	for _, endpoint := range []string{"192.168.1.5:50052", "example.com:50052", "0.0.0.0:50052", "127.0.0.1:0"} {
		if _, err := ServerArgsForHFRepo(8080, []string{endpoint}, "ggml-org/gemma-3-1b-it-GGUF"); err == nil { t.Fatalf("unsafe RPC endpoint accepted: %s", endpoint) }
	}
}

func TestModelPathAndHFRepoValidation(t *testing.T) {
	if _, err := ServerArgsForModel(8080, []string{"127.0.0.1:50052"}, ""); err == nil { t.Fatal("empty model path should fail") }
	if _, err := ServerArgsForHFRepo(8080, []string{"127.0.0.1:50052"}, "owner name/repo"); err == nil { t.Fatal("space in HF repo should fail") }
	if _, err := ServerArgsForHFRepo(8080, []string{"127.0.0.1:50052", "127.0.0.1:50052"}, "owner/repo"); err == nil { t.Fatal("duplicate RPC endpoint should fail") }
}
