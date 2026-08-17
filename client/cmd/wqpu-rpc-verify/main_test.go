package main

import (
	"testing"
)

func TestParseCheckpoint(t *testing.T) {
	block, hash, err := parseCheckpoint("123", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
	if err != nil {
		t.Fatal(err)
	}
	if block != 123 {
		t.Fatalf("block=%d want=123", block)
	}
	if hash.Hex() != "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" {
		t.Fatalf("unexpected hash %s", hash.Hex())
	}
}

func TestParseCheckpointRejectsMalformedValues(t *testing.T) {
	cases := [][2]string{
		{"0", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
		{"abc", "0x0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
		{"1", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
		{"1", "0xzz23456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"},
	}
	for _, tc := range cases {
		if _, _, err := parseCheckpoint(tc[0], tc[1]); err == nil {
			t.Fatalf("parseCheckpoint(%q,%q) unexpectedly succeeded", tc[0], tc[1])
		}
	}
}
