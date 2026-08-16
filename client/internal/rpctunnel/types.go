package rpctunnel

// LocalForwarder is the requester-side loopback listener that converts each
// local llama.cpp RPC connection into a fresh authenticated WQPU remote stream.
// It is an alias of Forwarder so the transport primitive keeps one lifecycle
// implementation while topology code can name the role explicitly.
type LocalForwarder = Forwarder
