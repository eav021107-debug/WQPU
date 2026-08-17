// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "../contracts/WQPUToken.sol";
import "../contracts/WQPURegistry.sol";
import "../contracts/WQPUComputeMarket.sol";

interface VmPermit {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
}

contract WQPUTokenPermitTest {
    VmPermit private constant vm = VmPermit(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant OWNER_KEY = 0xA11CE;
    address private owner;
    address private constant RELAYER = address(0xCAFE);

    WQPUToken private token;
    WQPURegistry private registry;
    WQPUComputeMarket private market;

    function setUp() public {
        owner = vm.addr(OWNER_KEY);
        token = new WQPUToken(1_000_000, owner);
        registry = new WQPURegistry(uint128(1 ether));
        market = new WQPUComputeMarket(address(token), address(registry));
    }

    function _permit(uint256 amount, uint256 deadline)
        private
        returns (bytes memory signature)
    {
        bytes32 structHash = keccak256(
            abi.encode(
                token.PERMIT_TYPEHASH(),
                owner,
                address(market),
                amount,
                token.nonces(owner),
                deadline
            )
        );
        bytes32 digest = keccak256(
            abi.encodePacked("\x19\x01", token.DOMAIN_SEPARATOR(), structHash)
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(OWNER_KEY, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function testRelayerCanFundOwnerEscrowWithPermit() public {
        uint256 amount = 7 ether;
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory signature = _permit(amount, deadline);

        vm.prank(RELAYER);
        market.depositWithPermit(owner, amount, deadline, signature);

        require(market.escrowBalance(owner) == amount, "escrow not credited to owner");
        require(token.balanceOf(address(market)) == amount, "market did not receive tokens");
        require(token.nonces(owner) == 1, "permit nonce not consumed");
        require(token.allowance(owner, address(market)) == 0, "permit allowance not consumed");
        require(token.balanceOf(RELAYER) == 0, "relayer received owner funds");
    }

    function testPermitCannotBeReplayed() public {
        uint256 amount = 2 ether;
        uint256 deadline = block.timestamp + 1 hours;
        bytes memory signature = _permit(amount, deadline);

        vm.prank(RELAYER);
        market.depositWithPermit(owner, amount, deadline, signature);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.depositWithPermit.selector,
                owner,
                amount,
                deadline,
                signature
            )
        );
        require(!ok, "permit replay succeeded");
        require(market.escrowBalance(owner) == amount, "replay changed escrow");
    }

    function testExpiredPermitCannotFundEscrow() public {
        uint256 amount = 1 ether;
        uint256 deadline = block.timestamp - 1;
        bytes memory signature = _permit(amount, deadline);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.depositWithPermit.selector,
                owner,
                amount,
                deadline,
                signature
            )
        );
        require(!ok, "expired permit succeeded");
        require(market.escrowBalance(owner) == 0, "expired permit changed escrow");
    }
}
