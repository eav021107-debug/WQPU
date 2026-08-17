// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title WQPU Token
/// @notice Fixed-supply ERC-20 used to pay for distributed compute.
/// @dev There is intentionally no mint function after deployment. EIP-2612 permit lets
///      a wallet authorize escrow funding with a signature instead of a separate approve tx.
contract WQPUToken {
    string public constant name = "WQPU";
    string public constant symbol = "WQPU";
    uint8 public constant decimals = 18;

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 public constant PERMIT_TYPEHASH = keccak256(
        "Permit(address owner,address spender,uint256 value,uint256 nonce,uint256 deadline)"
    );
    bytes32 private constant NAME_HASH = keccak256("WQPU");
    bytes32 private constant VERSION_HASH = keccak256("1");
    uint256 private constant HALF_ORDER =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    uint256 public immutable totalSupply;
    bytes32 public immutable DOMAIN_SEPARATOR;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    mapping(address => uint256) public nonces;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(uint256 fixedSupplyWholeTokens, address initialHolder) {
        require(initialHolder != address(0), "zero holder");
        require(fixedSupplyWholeTokens > 0, "zero supply");

        uint256 supply = fixedSupplyWholeTokens * 1 ether;
        totalSupply = supply;
        balanceOf[initialHolder] = supply;
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                NAME_HASH,
                VERSION_HASH,
                block.chainid,
                address(this)
            )
        );
        emit Transfer(address(0), initialHolder, supply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        _approve(msg.sender, spender, value);
        return true;
    }

    /// @notice EIP-2612 permit: approve by wallet signature, with replay protection.
    function permit(
        address owner,
        address spender,
        uint256 value,
        uint256 deadline,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        require(owner != address(0), "zero owner");
        require(block.timestamp <= deadline, "permit expired");
        require(uint256(s) <= HALF_ORDER, "bad signature s");
        require(v == 27 || v == 28, "bad signature v");

        uint256 nonce = nonces[owner]++;
        bytes32 structHash = keccak256(
            abi.encode(PERMIT_TYPEHASH, owner, spender, value, nonce, deadline)
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash)
        );
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0) && signer == owner, "bad permit");
        _approve(owner, spender, value);
    }

    function transferFrom(address from, address to, uint256 value) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            require(allowed >= value, "allowance");
            unchecked {
                allowance[from][msg.sender] = allowed - value;
            }
            emit Approval(from, msg.sender, allowance[from][msg.sender]);
        }
        _transfer(from, to, value);
        return true;
    }

    function _approve(address owner, address spender, uint256 value) internal {
        require(spender != address(0), "zero spender");
        allowance[owner][spender] = value;
        emit Approval(owner, spender, value);
    }

    function _transfer(address from, address to, uint256 value) internal {
        require(to != address(0), "zero recipient");
        uint256 balance = balanceOf[from];
        require(balance >= value, "balance");
        unchecked {
            balanceOf[from] = balance - value;
            balanceOf[to] += value;
        }
        emit Transfer(from, to, value);
    }
}
