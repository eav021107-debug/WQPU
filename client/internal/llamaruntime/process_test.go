package llamaruntime

import (
	"context"
	"net"
	"os"
	"path/filepath"
	goruntime "runtime"
	"strconv"
	"testing"
	"time"
)

func TestExecutableMustStayInsideInstalledRuntime(t *testing.T) {
	root := t.TempDir()
	inside := filepath.Join(root, "llama-server")
	if err := os.WriteFile(inside, []byte("binary"), 0o755); err != nil { t.Fatal(err) }
	if err := executableInside(root, inside); err != nil { t.Fatal(err) }

	outside := filepath.Join(t.TempDir(), "outside")
	if err := os.WriteFile(outside, []byte("outside"), 0o755); err != nil { t.Fatal(err) }
	if err := executableInside(root, outside); err == nil { t.Fatal("outside executable should fail") }

	if goruntime.GOOS != "windows" {
		link := filepath.Join(root, "escaped-link")
		if err := os.Symlink(outside, link); err != nil { t.Fatal(err) }
		if err := executableInside(root, link); err == nil { t.Fatal("symlink escape should fail") }
	}
}

func TestWaitLoopbackReadyUsesLiteralLocalListener(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	_, portText, err := net.SplitHostPort(listener.Addr().String())
	if err != nil { t.Fatal(err) }
	port, err := strconv.Atoi(portText)
	if err != nil { t.Fatal(err) }
	process := &ManagedProcess{done: make(chan struct{})}
	if err := waitLoopbackReady(context.Background(), process, port, time.Second); err != nil { t.Fatal(err) }
}

func TestManagedProcessReportsEarlyExit(t *testing.T) {
	if goruntime.GOOS == "windows" { t.Skip("shell fixture is Unix-only") }
	process, err := startManaged(context.Background(), "/bin/sh", []string{"-c", "exit 7"}, nil)
	if err != nil { t.Fatal(err) }
	if err := process.Wait(); err == nil { t.Fatal("non-zero child exit should be reported") }
}

func TestManagedProcessCloseCancelsLongRunningChild(t *testing.T) {
	if goruntime.GOOS == "windows" { t.Skip("sleep fixture is Unix-only") }
	process, err := startManaged(context.Background(), "/bin/sleep", []string{"30"}, nil)
	if err != nil { t.Fatal(err) }
	if process.PID() <= 0 { t.Fatal("managed child has no pid") }
	started := time.Now()
	if err := process.Close(); err != nil { t.Fatal(err) }
	if time.Since(started) > 5*time.Second { t.Fatal("managed child did not stop promptly") }
	select {
	case <-process.Done():
	default: t.Fatal("managed child did not close done channel")
	}
}
