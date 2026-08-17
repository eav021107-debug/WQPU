package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func abiWord(args []byte, index uint64) ([]byte, error) {
	if index > (^uint64(0)-32)/32 {
		return nil, errors.New("WQPU ABI word index overflow")
	}
	start := index * 32
	end := start + 32
	if start > uint64(len(args)) || end > uint64(len(args)) {
		return nil, errors.New("truncated WQPU ABI head")
	}
	return args[start:end], nil
}

func decodeAddressWord(word []byte) (common.Address, error) {
	if len(word) != 32 {
		return common.Address{}, errors.New("WQPU address requires one ABI word")
	}
	for _, b := range word[:12] {
		if b != 0 {
			return common.Address{}, errors.New("non-canonical WQPU ABI address")
		}
	}
	address := common.BytesToAddress(word[12:])
	if address == (common.Address{}) {
		return common.Address{}, errors.New("zero WQPU address")
	}
	return address, nil
}

func decodeABIUint64(args []byte, index uint64) (uint64, error) {
	word, err := abiWord(args, index)
	if err != nil {
		return 0, err
	}
	return decodeUint256Word(word)
}

func decodeABIAddress(args []byte, index uint64) (common.Address, error) {
	word, err := abiWord(args, index)
	if err != nil {
		return common.Address{}, err
	}
	return decodeAddressWord(word)
}

// decodeDynamicBytes accepts only canonical ABI: aligned offset after the
// complete head, exact total length and zero padding. This prevents several
// byte strings from representing the same signed WQPU action.
func decodeDynamicBytes(args []byte, offsetWord uint64, headWords uint64, maxBytes uint64) ([]byte, error) {
	offset, err := decodeABIUint64(args, offsetWord)
	if err != nil {
		return nil, err
	}
	minimum := headWords * 32
	if offset < minimum || offset%32 != 0 || offset > uint64(len(args)) || offset+32 < offset || offset+32 > uint64(len(args)) {
		return nil, errors.New("invalid WQPU ABI dynamic offset")
	}
	length, err := decodeUint256Word(args[offset : offset+32])
	if err != nil {
		return nil, err
	}
	if length > maxBytes {
		return nil, errors.New("WQPU ABI byte string exceeds protocol bound")
	}
	padded := ((length + 31) / 32) * 32
	end := offset + 32 + padded
	if end < offset || end != uint64(len(args)) {
		return nil, errors.New("non-canonical WQPU ABI dynamic length")
	}
	dataEnd := offset + 32 + length
	for _, b := range args[dataEnd:end] {
		if b != 0 {
			return nil, errors.New("non-zero WQPU ABI padding")
		}
	}
	return append([]byte(nil), args[offset+32:dataEnd]...), nil
}
