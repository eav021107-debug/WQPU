// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "../contracts/WQPUToken.sol";
import "../contracts/WQPURegistry.sol";
import "../contracts/WQPUComputeMarket.sol";

interface VmSessionEscrow {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
}

contract WQPUSessionEscrowTest {
    VmSessionEscrow private constant vm = VmSessionEscrow(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant REQUESTER_KEY = 0xA11CE;
    uint256 private constant SESSION_KEY = 0x515510;

    address private requester;
    address private sessionKey;
    address private constant PROVIDER_A = address(0xBEEF);
    address private constant PROVIDER_B = address(0xF00D);
    address private constant RELAYER = address(0xCAFE);

    WQPUToken private token;
    WQPURegistry private registry;
    WQPUComputeMarket private market;

    function setUp() public {
        requester = vm.addr(REQUESTER_KEY);
        sessionKey = vm.addr(SESSION_KEY);
        token = new WQPUToken(1_000_000, requester);
        registry = new WQPURegistry(uint128(1 ether));
        market = new WQPUComputeMarket(address(token), address(registry));

        vm.prank(requester);
        token.approve(address(market), type(uint256).max);
        vm.prank(requester);
        market.deposit(10 ether);
    }

    function _auth(bytes32 sessionId, uint128 maxAmount, uint128 price, uint64 validUntil)
        private
        returns (bytes memory)
    {
        bytes32 digest = market.spendAuthorizationDigest(
            requester, sessionKey, sessionId, maxAmount, price, validUntil
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(REQUESTER_KEY, digest);
        return abi.encodePacked(r, s, v);
    }

    function _voucher(
        address provider,
        bytes32 sessionId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits
    ) private returns (bytes memory) {
        bytes32 digest = market.providerVoucherDigest(
            requester, provider, sessionId, cumulativeAmount, cumulativeUnits
        );
        (, bytes32 r, bytes32 s) = vm.sign(SESSION_KEY, digest);
        return abi.encodePacked(r, s);
    }

    function _claim(
        address provider,
        bytes32 sessionId,
        uint256 amount,
        uint256 units,
        uint128 maxAmount,
        uint128 price,
        uint64 validUntil,
        bytes memory auth
    ) private {
        bytes memory voucher = _voucher(provider, sessionId, amount, units);
        vm.prank(RELAYER);
        market.claimEscrowWithSession(
            requester,
            provider,
            sessionId,
            amount,
            units,
            voucher,
            sessionKey,
            maxAmount,
            price,
            validUntil,
            auth
        );
    }

    function testOneDepositPaysMultipleProvidersWithoutNewWalletTransactions() public {
        bytes32 sessionId = keccak256("shared-escrow-session");
        uint128 maxAmount = uint128(5 ether);
        uint128 price = uint128(1 ether);
        uint64 validUntil = uint64(block.timestamp + 1 days);
        bytes memory auth = _auth(sessionId, maxAmount, price, validUntil);

        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000, maxAmount, price, validUntil, auth);
        _claim(PROVIDER_B, sessionId, 2 ether, 2_000_000, maxAmount, price, validUntil, auth);

        require(token.balanceOf(PROVIDER_A) == 1 ether, "provider A not paid");
        require(token.balanceOf(PROVIDER_B) == 2 ether, "provider B not paid");
        require(market.escrowBalance(requester) == 7 ether, "wrong remaining escrow");
        require(market.sessionSpent(requester, sessionId) == 3 ether, "wrong session spend");
        require(token.balanceOf(RELAYER) == 0, "relayer received provider funds");
    }

    function testPriceChangeStopsOldSessionInsteadOfChargingNewPrice() public {
        bytes32 sessionId = keccak256("price-locked-session");
        uint128 maxAmount = uint128(5 ether);
        uint128 oldPrice = uint128(1 ether);
        uint64 validUntil = uint64(block.timestamp + 1 days);
        bytes memory auth = _auth(sessionId, maxAmount, oldPrice, validUntil);

        registry.setGlobalPrice(uint128(2 ether));
        bytes memory voucher = _voucher(PROVIDER_A, sessionId, 1 ether, 1_000_000);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.claimEscrowWithSession.selector,
                requester,
                PROVIDER_A,
                sessionId,
                1 ether,
                1_000_000,
                voucher,
                sessionKey,
                maxAmount,
                oldPrice,
                validUntil,
                auth
            )
        );

        require(!ok, "old session survived network repricing");
        require(token.balanceOf(PROVIDER_A) == 0, "provider paid at stale price");
        require(market.escrowBalance(requester) == 10 ether, "failed claim changed escrow");
    }
}
