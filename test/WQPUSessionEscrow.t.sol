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
    function warp(uint256 timestamp) external;
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

    function _authData(bytes32 sessionId, uint128 maxAmount, uint128 price, uint64 validUntil)
        private
        view
        returns (WQPUComputeMarket.SpendAuthorizationData memory auth)
    {
        auth = WQPUComputeMarket.SpendAuthorizationData({
            requester: requester,
            sessionKey: sessionKey,
            sessionId: sessionId,
            maxAmount: maxAmount,
            pricePerMillionUnits: price,
            validUntil: validUntil
        });
    }

    function _authSignature(bytes32 sessionId, uint128 maxAmount, uint128 price, uint64 validUntil)
        private
        returns (bytes memory)
    {
        bytes32 digest = market.spendAuthorizationDigest(
            requester, sessionKey, sessionId, maxAmount, price, validUntil
        );
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(REQUESTER_KEY, digest);
        return abi.encodePacked(r, s, v);
    }

    function _activate(bytes32 sessionId, uint128 maxAmount, uint128 price, uint64 validUntil)
        private
        returns (bytes memory authorization)
    {
        WQPUComputeMarket.SpendAuthorizationData memory auth =
            _authData(sessionId, maxAmount, price, validUntil);
        authorization = _authSignature(sessionId, maxAmount, price, validUntil);
        vm.prank(RELAYER);
        market.activateSession(auth, authorization);
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
        uint256 units
    ) private {
        WQPUComputeMarket.ProviderVoucherData memory voucher = WQPUComputeMarket.ProviderVoucherData({
            provider: provider,
            cumulativeAmount: amount,
            cumulativeUnits: units
        });
        bytes memory voucherSignature = _voucher(provider, sessionId, amount, units);
        vm.prank(RELAYER);
        market.claimEscrowWithSession(requester, sessionId, voucher, voucherSignature);
    }

    function _tryClaim(
        address provider,
        bytes32 sessionId,
        uint256 amount,
        uint256 units
    ) private returns (bool ok) {
        WQPUComputeMarket.ProviderVoucherData memory voucher = WQPUComputeMarket.ProviderVoucherData({
            provider: provider,
            cumulativeAmount: amount,
            cumulativeUnits: units
        });
        bytes memory voucherSignature = _voucher(provider, sessionId, amount, units);
        vm.prank(RELAYER);
        (ok,) = address(market).call(
            abi.encodeWithSelector(
                market.claimEscrowWithSession.selector,
                requester,
                sessionId,
                voucher,
                voucherSignature
            )
        );
    }

    function testActivationReservesEscrowAndBlocksWithdrawal() public {
        bytes32 sessionId = keccak256("reserved-session");
        uint64 validUntil = uint64(block.timestamp + 1 days);
        _activate(sessionId, uint128(6 ether), uint128(1 ether), validUntil);

        require(market.reservedEscrow(requester) == 6 ether, "reserve missing");

        vm.prank(requester);
        (bool tooMuch,) = address(market).call(
            abi.encodeWithSelector(market.withdraw.selector, 5 ether)
        );
        require(!tooMuch, "reserved funds withdrawn");

        vm.prank(requester);
        market.withdraw(4 ether);
        require(market.escrowBalance(requester) == 6 ether, "free escrow withdrawal wrong");
        require(market.reservedEscrow(requester) == 6 ether, "reserve changed by free withdrawal");
    }

    function testOneActivationPaysMultipleProvidersWithoutNewWalletTransactions() public {
        bytes32 sessionId = keccak256("shared-escrow-session");
        uint128 maxAmount = uint128(5 ether);
        uint64 validUntil = uint64(block.timestamp + 1 days);
        _activate(sessionId, maxAmount, uint128(1 ether), validUntil);

        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000);
        _claim(PROVIDER_B, sessionId, 2 ether, 2_000_000);

        require(token.balanceOf(PROVIDER_A) == 1 ether, "provider A not paid");
        require(token.balanceOf(PROVIDER_B) == 2 ether, "provider B not paid");
        require(market.escrowBalance(requester) == 7 ether, "wrong remaining escrow");
        require(market.reservedEscrow(requester) == 2 ether, "wrong remaining reserve");
        require(market.sessionSpent(requester, sessionId) == 3 ether, "wrong session spend");
        require(token.balanceOf(RELAYER) == 0, "relayer received provider funds");
    }

    function testCumulativeProviderVoucherCannotReplay() public {
        bytes32 sessionId = keccak256("replay-session");
        _activate(sessionId, uint128(5 ether), uint128(1 ether), uint64(block.timestamp + 1 days));

        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000);
        bool replay = _tryClaim(PROVIDER_A, sessionId, 1 ether, 1_000_000);
        require(!replay, "cumulative voucher replay succeeded");
        require(token.balanceOf(PROVIDER_A) == 1 ether, "provider double-paid");
    }

    function testSessionCannotExceedReservedLimit() public {
        bytes32 sessionId = keccak256("small-session");
        _activate(sessionId, uint128(1 ether), uint128(1 ether), uint64(block.timestamp + 1 days));

        bool ok = _tryClaim(PROVIDER_A, sessionId, 2 ether, 2_000_000);
        require(!ok, "session exceeded reserved limit");
        require(token.balanceOf(PROVIDER_A) == 0, "provider paid above limit");
        require(market.escrowBalance(requester) == 10 ether, "failed claim consumed escrow");
        require(market.reservedEscrow(requester) == 1 ether, "failed claim consumed reserve");
    }

    function testActiveSessionCannotBeRevokedToCheatProvider() public {
        bytes32 sessionId = keccak256("provider-guarantee");
        _activate(sessionId, uint128(5 ether), uint128(1 ether), uint64(block.timestamp + 1 days));

        vm.prank(requester);
        (bool revoked,) = address(market).call(
            abi.encodeWithSelector(market.revokeSession.selector, sessionId)
        );
        require(!revoked, "active session revoked early");

        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000);
        require(token.balanceOf(PROVIDER_A) == 1 ether, "provider lost earned voucher");
    }

    function testGlobalPriceChangeDoesNotRewriteActiveSessionPrice() public {
        bytes32 sessionId = keccak256("price-snapshot-session");
        _activate(sessionId, uint128(5 ether), uint128(1 ether), uint64(block.timestamp + 1 days));

        registry.setGlobalPrice(uint128(2 ether));
        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000);

        require(token.balanceOf(PROVIDER_A) == 1 ether, "active session repriced");
    }

    function testUnusedReserveReleasesOnlyAfterExpiryAndClaimGrace() public {
        bytes32 sessionId = keccak256("release-session");
        uint64 validUntil = uint64(block.timestamp + 1 hours);
        _activate(sessionId, uint128(5 ether), uint128(1 ether), validUntil);
        _claim(PROVIDER_A, sessionId, 1 ether, 1_000_000);

        vm.warp(uint256(validUntil) + market.CLAIM_GRACE() + 1);
        vm.prank(requester);
        market.releaseSession(sessionId);

        require(market.reservedEscrow(requester) == 0, "reserve not released");
        vm.prank(requester);
        market.withdraw(9 ether);
        require(market.escrowBalance(requester) == 0, "released escrow not withdrawable");
    }
}
