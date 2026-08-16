package llamaruntime

import (
	"strings"
	"testing"
)

func TestServerArgsForHFFilePinsRemoteRPCDevice(t *testing.T) {
	args, err := ServerArgsForHFFile(
		8081,
		[]string{"127.0.0.1:41001"},
		"ggml-org/models",
		"tinyllamas/stories260K.gguf",
		ServerTuning{Devices: []string{"RPC0"}, GPULayers: 99, ContextSize: 128, Parallel: 1, Threads: 1},
	)
	if err != nil { t.Fatal(err) }
	joined := strings.Join(args, " ")
	for _, want := range []string{
		"--host 127.0.0.1",
		"--port 8081",
		"--rpc 127.0.0.1:41001",
		"--fit on",
		"--hf-repo ggml-org/models",
		"--hf-file tinyllamas/stories260K.gguf",
		"--device RPC0",
		"--n-gpu-layers 99",
		"--ctx-size 128",
		"--parallel 1",
		"--threads 1",
	} {
		if !strings.Contains(joined, want) { t.Fatalf("missing %q in %v", want, args) }
	}
	if strings.Index(joined, "--rpc") > strings.Index(joined, "--device RPC0") {
		t.Fatalf("RPC backend must be registered before RPC0 device selection: %v", args)
	}
}

func TestServerArgsForHFFileRejectsUnsafeInputs(t *testing.T) {
	baseRPC := []string{"127.0.0.1:41001"}
	for _, file := range []string{"", "../model.gguf", "tiny/../model.gguf", "/tmp/model.gguf", "tiny\\model.gguf", " model.gguf"} {
		if _, err := ServerArgsForHFFile(8081, baseRPC, "ggml-org/models", file, ServerTuning{}); err == nil {
			t.Fatalf("unsafe HF file accepted: %q", file)
		}
	}
	if _, err := ServerArgsForHFFile(8081, baseRPC, "ggml-org/models", "tiny/model.gguf", ServerTuning{Devices: []string{"RPC0", "RPC0"}}); err == nil {
		t.Fatal("duplicate offload device should fail")
	}
	if _, err := ServerArgsForHFFile(8081, baseRPC, "ggml-org/models", "tiny/model.gguf", ServerTuning{Devices: []string{"RPC0,CPU"}}); err == nil {
		t.Fatal("ambiguous comma-containing device should fail")
	}
	if _, err := ServerArgsForHFFile(8081, baseRPC, "ggml-org/models", "tiny/model.gguf", ServerTuning{GPULayers: -1}); err == nil {
		t.Fatal("negative tuning should fail")
	}
}
