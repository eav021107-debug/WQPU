package llamaruntime

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const DefaultReadinessTimeout = 30 * time.Second

type ManagedProcess struct {
	cmd    *exec.Cmd
	cancel context.CancelFunc
	done   chan struct{}

	mu      sync.Mutex
	waitErr error
	once    sync.Once
}

func executableInside(root, binary string) error {
	if root == "" || binary == "" { return errors.New("WQPU llama.cpp runtime paths are required") }
	rootReal, err := filepath.EvalSymlinks(root)
	if err != nil { return err }
	rootReal, err = filepath.Abs(rootReal)
	if err != nil { return err }
	binaryReal, err := filepath.EvalSymlinks(binary)
	if err != nil { return err }
	binaryReal, err = filepath.Abs(binaryReal)
	if err != nil { return err }
	prefix := rootReal + string(os.PathSeparator)
	if binaryReal == rootReal || !strings.HasPrefix(binaryReal, prefix) {
		return errors.New("llama.cpp executable escapes installed runtime")
	}
	info, err := os.Stat(binaryReal)
	if err != nil { return err }
	if !info.Mode().IsRegular() { return errors.New("llama.cpp executable is not a regular file") }
	return nil
}

func startManaged(parent context.Context, binary string, args []string, output io.Writer) (*ManagedProcess, error) {
	if parent == nil { return nil, errors.New("WQPU process context is required") }
	select {
	case <-parent.Done(): return nil, parent.Err()
	default:
	}
	ctx, cancel := context.WithCancel(parent)
	cmd := exec.CommandContext(ctx, binary, args...)
	if output == nil { output = io.Discard }
	cmd.Stdout = output
	cmd.Stderr = output
	if err := cmd.Start(); err != nil { cancel(); return nil, err }
	process := &ManagedProcess{cmd: cmd, cancel: cancel, done: make(chan struct{})}
	go func() {
		err := cmd.Wait()
		process.mu.Lock()
		if ctx.Err() != nil { err = nil }
		process.waitErr = err
		process.mu.Unlock()
		close(process.done)
	}()
	return process, nil
}

func (p *ManagedProcess) PID() int {
	if p == nil || p.cmd == nil || p.cmd.Process == nil { return 0 }
	return p.cmd.Process.Pid
}

func (p *ManagedProcess) Done() <-chan struct{} {
	if p == nil {
		ch := make(chan struct{})
		close(ch)
		return ch
	}
	return p.done
}

func (p *ManagedProcess) Wait() error {
	if p == nil { return nil }
	<-p.done
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.waitErr
}

func (p *ManagedProcess) Close() error {
	if p == nil { return nil }
	p.once.Do(func() { p.cancel() })
	return p.Wait()
}

func waitLoopbackReady(parent context.Context, process *ManagedProcess, port int, timeout time.Duration) error {
	if process == nil { return errors.New("WQPU llama.cpp process is required") }
	if err := validatePort(port); err != nil { return err }
	if timeout <= 0 { timeout = DefaultReadinessTimeout }
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	address := net.JoinHostPort("127.0.0.1", strconv.Itoa(port))
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for {
		connection, err := (&net.Dialer{Timeout: 250 * time.Millisecond}).DialContext(ctx, "tcp", address)
		if err == nil {
			_ = connection.Close()
			return nil
		}
		select {
		case <-process.Done():
			if waitErr := process.Wait(); waitErr != nil { return fmt.Errorf("llama.cpp process exited before readiness: %w", waitErr) }
			return errors.New("llama.cpp process exited before readiness")
		case <-ctx.Done():
			_ = process.Close()
			return fmt.Errorf("llama.cpp loopback listener %s was not ready: %w", address, ctx.Err())
		case <-ticker.C:
		}
	}
}

func StartRPCServer(parent context.Context, runtime Runtime, port, threads int, devices []string, cache bool, output io.Writer, readiness time.Duration) (*ManagedProcess, error) {
	if err := executableInside(runtime.Root, runtime.RPCServer); err != nil { return nil, err }
	args, err := RPCServerArgs(port, threads, devices, cache)
	if err != nil { return nil, err }
	process, err := startManaged(parent, runtime.RPCServer, args, output)
	if err != nil { return nil, err }
	if err := waitLoopbackReady(parent, process, port, readiness); err != nil { return nil, err }
	return process, nil
}

func StartLlamaServerForModel(parent context.Context, runtime Runtime, apiPort int, rpcEndpoints []string, modelPath string, output io.Writer, readiness time.Duration) (*ManagedProcess, error) {
	if err := executableInside(runtime.Root, runtime.LlamaServer); err != nil { return nil, err }
	args, err := ServerArgsForModel(apiPort, rpcEndpoints, modelPath)
	if err != nil { return nil, err }
	process, err := startManaged(parent, runtime.LlamaServer, args, output)
	if err != nil { return nil, err }
	if err := waitLoopbackReady(parent, process, apiPort, readiness); err != nil { return nil, err }
	return process, nil
}

func StartLlamaServerForHFRepo(parent context.Context, runtime Runtime, apiPort int, rpcEndpoints []string, repo string, output io.Writer, readiness time.Duration) (*ManagedProcess, error) {
	if err := executableInside(runtime.Root, runtime.LlamaServer); err != nil { return nil, err }
	args, err := ServerArgsForHFRepo(apiPort, rpcEndpoints, repo)
	if err != nil { return nil, err }
	process, err := startManaged(parent, runtime.LlamaServer, args, output)
	if err != nil { return nil, err }
	if err := waitLoopbackReady(parent, process, apiPort, readiness); err != nil { return nil, err }
	return process, nil
}
