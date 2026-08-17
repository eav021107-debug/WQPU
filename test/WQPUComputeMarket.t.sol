// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "../contracts/WQPUToken.sol";
import "../contracts/WQPURegistry.sol";
import "../contracts/WQPUComputeMarket.sol";

interface Vm {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
}

contract WQPUComputeMarketTest {
    Vm private constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    uint256 private constant REQUESTER_KEY = 0xA11CE;
    address private requester;
    address private constant PROVIDER = address(0xBEEF);
    address private constant RELAYER = address(0xCAFE);

    WQPUToken private token;
    WQPURegistry private registry;
    WQPUComputeMarket private market;

    function setUp() public {
        requester = vm.addr(REQUESTER_KEY);
        token = new WQPUToken(1_000_000, requester);
        registry = new WQPURegistry(uint128(1 ether));
        market = new WQPUComputeMarket(address(token), address(registry));

        vm.prank(requester);
        token.approve(address(market), type(uint256).max);
    }

    function _open(uint256 deposit) private returns (bytes32 channelId) {
        vm.prank(requester);
        channelId = market.openChannel(
            PROVIDER,
            deposit,
            uint64(block.timestamp + 1 days)
        );
    }

    function _voucher(bytes32 channelId, uint256 amount, uint256 units)
        private
        returns (bytes memory signature)
    {
        bytes32 digest = market.voucherDigest(channelId, amount, units);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(REQUESTER_KEY, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function testChannelSnapshotsOneGlobalPrice() public {
        bytes32 first = _open(10 ether);
        require(market.amountForUnits(first, 1_000_000) == 1 ether, "wrong first price");

        registry.setGlobalPrice(uint128(2 ether));
        require(market.amountForUnits(first, 1_000_000) == 1 ether, "old channel repriced");

        bytes32 second = _open(10 ether);
        require(market.amountForUnits(second, 1_000_000) == 2 ether, "new price not captured");
    }

    function testAnyRelayerCanSubmitValidVoucherButProviderGetsPaid() public {
        bytes32 channelId = _open(10 ether);
        uint256 units = 2_000_000;
        uint256 amount = market.amountForUnits(channelId, units);
        bytes memory signature = _voucher(channelId, amount, units);

        vm.prank(RELAYER);
        market.claim(channelId, amount, units, signature);

        require(token.balanceOf(PROVIDER) == amount, "provider not paid");
        require(token.balanceOf(RELAYER) == 0, "relayer received provider funds");

        vm.prank(RELAYER);
        (bool replayOk,) = address(market).call(
            abi.encodeWithSelector(market.claim.selector, channelId, amount, units, signature)
        );
        require(!replayOk, "voucher replay succeeded");
    }

    function testVoucherCannotOverrideNetworkPrice() public {
        bytes32 channelId = _open(10 ether);
        uint256 units = 1_000_000;
        uint256 correctAmount = market.amountForUnits(channelId, units);
        bytes memory signature = _voucher(channelId, correctAmount + 1, units);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.claim.selector,
                channelId,
                correctAmount + 1,
                units,
                signature
            )
        );
        require(!ok, "wrong price voucher succeeded");
        require(token.balanceOf(PROVIDER) == 0, "provider paid wrong amount");
    }
}
