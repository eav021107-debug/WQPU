package main

import (
	"context"
	"fmt"
	"os"
	"runtime"
	"time"

	"github.com/eav021107-debug/WQPU/client/internal/llamaruntime"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-runtime-install BASE_DIR")
		os.Exit(2)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	installed, err := (llamaruntime.Installer{}).InstallCPU(ctx, os.Args[1], runtime.GOOS, runtime.GOARCH)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("tag=%s\nroot=%s\nrpc=%s\nserver=%s\ncli=%s\n", installed.Tag, installed.Root, installed.RPCServer, installed.LlamaServer, installed.LlamaCLI)
}
