// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title WQPU Token
/// @notice Fixed-supply ERC-20 used to pay for distributed compute.
/// @dev There is intentionally no mint function after deployment.
contract WQPUToken {
    string public constant name = "WQPU";
    string public constant symbol = "WQPU";
    uint8 public constant decimals = 18;

    uint256 public immutable totalSupply;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    constructor(uint256 fixedSupplyWholeTokens, address initialHolder) {
        require(initialHolder != address(0), "zero holder");
        require(fixedSupplyWholeTokens > 0, "zero supply");

        uint256 supply = fixedSupplyWholeTokens * 1 ether;
        totalSupply = supply;
        balanceOf[initialHolder] = supply;
        emit Transfer(address(0), initialHolder, supply);
    }

    function transfer(address to, uint256 value) external returns (bool) {
        _transfer(msg.sender, to, value);
        return true;
    }

    function approve(address spender, uint256 value) external returns (bool) {
        allowance[msg.sender][spender] = value;
        emit Approval(msg.sender, spender, value);
        return true;
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
