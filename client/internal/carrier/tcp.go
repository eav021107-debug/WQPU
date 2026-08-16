package carrier

import (
	"context"
	"errors"
	"io"
	"net"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const DefaultDialTimeout = 10 * time.Second

type Dialer interface {
	DialContext(context.Context, string) (io.ReadWriteCloser, error)
}

type TCPDialer struct {
	Timeout time.Duration
}

func ParseEndpoint(endpoint string) (string, error) {
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "wqpu" || u.User != nil || u.Hostname() == "" || u.Port() == "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return "", errors.New("invalid WQPU endpoint")
	}
	if strings.ContainsAny(u.Hostname(), " /\\") { return "", errors.New("invalid WQPU endpoint host") }
	port, err := strconv.ParseUint(u.Port(), 10, 16)
	if err != nil || port == 0 { return "", errors.New("invalid WQPU endpoint port") }
	return net.JoinHostPort(u.Hostname(), strconv.FormatUint(port, 10)), nil
}

func (d TCPDialer) DialContext(ctx context.Context, endpoint string) (io.ReadWriteCloser, error) {
	hostPort, err := ParseEndpoint(endpoint)
	if err != nil { return nil, err }
	timeout := d.Timeout
	if timeout <= 0 { timeout = DefaultDialTimeout }
	dialer := net.Dialer{Timeout: timeout, KeepAlive: 30 * time.Second}
	conn, err := dialer.DialContext(ctx, "tcp", hostPort)
	if err != nil { return nil, err }
	return conn, nil
}

func Listen(ctx context.Context, endpoint string) (net.Listener, error) {
	hostPort, err := ParseEndpoint(endpoint)
	if err != nil { return nil, err }
	var config net.ListenConfig
	return config.Listen(ctx, "tcp", hostPort)
}
