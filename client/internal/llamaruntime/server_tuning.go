package llamaruntime

import (
	"errors"
	"strconv"
	"strings"
)

type ServerTuning struct {
	Devices     []string
	GPULayers   int
	ContextSize int
	Parallel    int
	Threads     int
	SplitMode   string
	TensorSplit []uint64
}

func validateHFFile(file string) error {
	if file == "" || len(file) > 512 || strings.ContainsRune(file, '\x00') || strings.HasPrefix(file, "/") || strings.Contains(file, "\\") {
		return errors.New("valid Hugging Face file path is required")
	}
	clean := strings.TrimSpace(file)
	if clean != file || clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || strings.Contains(clean, "/../") {
		return errors.New("Hugging Face file path cannot traverse")
	}
	return nil
}

func appendServerTuning(args []string, tuning ServerTuning) ([]string, error) {
	if tuning.GPULayers < 0 || tuning.ContextSize < 0 || tuning.Parallel < 0 || tuning.Threads < 0 {
		return nil, errors.New("llama.cpp server tuning values cannot be negative")
	}
	if len(tuning.Devices) > 0 {
		seen := make(map[string]struct{}, len(tuning.Devices))
		for _, device := range tuning.Devices {
			if err := validateDevice(device); err != nil { return nil, err }
			if _, ok := seen[device]; ok { return nil, errors.New("duplicate llama.cpp offload device") }
			seen[device] = struct{}{}
		}
		args = append(args, "--device", strings.Join(tuning.Devices, ","))
	}
	if tuning.SplitMode != "" {
		switch tuning.SplitMode {
		case "none", "layer", "row", "tensor":
		default:
			return nil, errors.New("unsupported llama.cpp split mode")
		}
		args = append(args, "--split-mode", tuning.SplitMode)
	}
	if len(tuning.TensorSplit) > 0 {
		if len(tuning.Devices) > 0 && len(tuning.TensorSplit) != len(tuning.Devices) {
			return nil, errors.New("tensor split must match the selected offload device count")
		}
		parts := make([]string, len(tuning.TensorSplit))
		for index, value := range tuning.TensorSplit {
			if value == 0 { return nil, errors.New("tensor split proportions must be positive") }
			parts[index] = strconv.FormatUint(value, 10)
		}
		args = append(args, "--tensor-split", strings.Join(parts, ","))
	}
	if tuning.GPULayers > 0 { args = append(args, "--n-gpu-layers", strconv.Itoa(tuning.GPULayers)) }
	if tuning.ContextSize > 0 { args = append(args, "--ctx-size", strconv.Itoa(tuning.ContextSize)) }
	if tuning.Parallel > 0 { args = append(args, "--parallel", strconv.Itoa(tuning.Parallel)) }
	if tuning.Threads > 0 { args = append(args, "--threads", strconv.Itoa(tuning.Threads)) }
	return args, nil
}

func ServerArgsForHFFile(apiPort int, rpcEndpoints []string, repo, file string, tuning ServerTuning) ([]string, error) {
	if err := validateHFFile(file); err != nil { return nil, err }
	args, err := ServerArgsForHFRepo(apiPort, rpcEndpoints, repo)
	if err != nil { return nil, err }
	args = append(args, "--hf-file", file)
	return appendServerTuning(args, tuning)
}
