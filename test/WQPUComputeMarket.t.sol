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
    uint256 private constant SESSION_PRIVATE_KEY = 0x515510;
    address private requester;
    address private sessionKey;
    address private constant PROVIDER = address(0xBEEF);
    address private constant RELAYER = address(0xCAFE);

    WQPUToken private token;
    WQPURegistry private registry;
    WQPUComputeMarket private market;

    function setUp() public {
        requester = vm.addr(REQUESTER_KEY);
        sessionKey = vm.addr(SESSION_PRIVATE_KEY);
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

    function _sign(uint256 privateKey, bytes32 digest)
        private
        returns (bytes memory signature)
    {
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(privateKey, digest);
        signature = abi.encodePacked(r, s, v);
    }

    function _voucher(bytes32 channelId, uint256 amount, uint256 units)
        private
        returns (bytes memory signature)
    {
        return _sign(REQUESTER_KEY, market.voucherDigest(channelId, amount, units));
    }

    function _sessionVoucher(bytes32 channelId, uint256 amount, uint256 units)
        private
        returns (bytes memory signature)
    {
        return _sign(SESSION_PRIVATE_KEY, market.voucherDigest(channelId, amount, units));
    }

    function _sessionAuthorization(bytes32 sessionId, uint128 maxAmount, uint64 validUntil)
        private
        returns (bytes memory signature)
    {
        bytes32 digest = market.sessionAuthorizationDigest(
            requester,
            sessionKey,
            sessionId,
            maxAmount,
            validUntil
        );
        return _sign(REQUESTER_KEY, digest);
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

    function testSessionAuthorizationAllowsAutomaticBoundedVouchers() public {
        bytes32 channelId = _open(10 ether);
        bytes32 sessionId = keccak256("laptop-session-1");
        uint128 maxAmount = uint128(3 ether);
        uint64 validUntil = uint64(block.timestamp + 2 hours);
        bytes memory authorization = _sessionAuthorization(sessionId, maxAmount, validUntil);

        uint256 firstUnits = 1_000_000;
        uint256 firstAmount = market.amountForUnits(channelId, firstUnits);
        bytes memory firstVoucher = _sessionVoucher(channelId, firstAmount, firstUnits);

        vm.prank(RELAYER);
        market.claimWithSession(
            channelId,
            firstAmount,
            firstUnits,
            firstVoucher,
            sessionKey,
            sessionId,
            maxAmount,
            validUntil,
            authorization
        );

        uint256 secondUnits = 2_500_000;
        uint256 secondAmount = market.amountForUnits(channelId, secondUnits);
        bytes memory secondVoucher = _sessionVoucher(channelId, secondAmount, secondUnits);

        vm.prank(RELAYER);
        market.claimWithSession(
            channelId,
            secondAmount,
            secondUnits,
            secondVoucher,
            sessionKey,
            sessionId,
            maxAmount,
            validUntil,
            authorization
        );

        require(token.balanceOf(PROVIDER) == secondAmount, "session provider payout wrong");
        require(market.sessionSpent(requester, sessionId) == secondAmount, "session spend wrong");
        require(token.balanceOf(RELAYER) == 0, "relayer stole session payout");
    }

    function testSessionCannotExceedAuthorizedSpend() public {
        bytes32 channelId = _open(10 ether);
        bytes32 sessionId = keccak256("small-session");
        uint128 maxAmount = uint128(1 ether);
        uint64 validUntil = uint64(block.timestamp + 2 hours);
        bytes memory authorization = _sessionAuthorization(sessionId, maxAmount, validUntil);

        uint256 units = 2_000_000;
        uint256 amount = market.amountForUnits(channelId, units);
        bytes memory voucher = _sessionVoucher(channelId, amount, units);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.claimWithSession.selector,
                channelId,
                amount,
                units,
                voucher,
                sessionKey,
                sessionId,
                maxAmount,
                validUntil,
                authorization
            )
        );

        require(!ok, "session exceeded limit");
        require(token.balanceOf(PROVIDER) == 0, "provider paid above session limit");
        require(market.sessionSpent(requester, sessionId) == 0, "failed claim consumed session limit");
    }

    function testRequesterCanRevokeSession() public {
        bytes32 channelId = _open(10 ether);
        bytes32 sessionId = keccak256("revoked-session");
        uint128 maxAmount = uint128(5 ether);
        uint64 validUntil = uint64(block.timestamp + 2 hours);
        bytes memory authorization = _sessionAuthorization(sessionId, maxAmount, validUntil);

        vm.prank(requester);
        market.revokeSession(sessionId);

        uint256 units = 1_000_000;
        uint256 amount = market.amountForUnits(channelId, units);
        bytes memory voucher = _sessionVoucher(channelId, amount, units);

        vm.prank(RELAYER);
        (bool ok,) = address(market).call(
            abi.encodeWithSelector(
                market.claimWithSession.selector,
                channelId,
                amount,
                units,
                voucher,
                sessionKey,
                sessionId,
                maxAmount,
                validUntil,
                authorization
            )
        );

        require(!ok, "revoked session still worked");
        require(token.balanceOf(PROVIDER) == 0, "revoked session paid provider");
    }
}
