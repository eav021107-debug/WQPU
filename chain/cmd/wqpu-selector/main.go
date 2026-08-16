package main

import (
	"fmt"
	"os"

	"github.com/ethereum/go-ethereum/crypto"
)

func main() {
	if len(os.Args) != 2 || os.Args[1] == "" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-selector 'method(type,...)'")
		os.Exit(2)
	}
	hash := crypto.Keccak256([]byte(os.Args[1]))
	fmt.Printf("%x\n", hash[:4])
}
