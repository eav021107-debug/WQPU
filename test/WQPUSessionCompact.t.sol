// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "../contracts/WQPUToken.sol";
import "../contracts/WQPURegistry.sol";
import "../contracts/WQPUComputeMarket.sol";

interface VmCompactSession {
    function addr(uint256 privateKey) external returns (address);
    function sign(uint256 privateKey, bytes32 digest)
        external
        returns (uint8 v, bytes32 r, bytes32 s);
    function prank(address sender) external;
}

contract WQPUSessionCompactTest {
    VmCompactSession private constant vm = VmCompactSession(address(uint160(uint256(keccak256("hevm cheat code")))));
    uint256 private constant REQUESTER_KEY = 0xA11CE;
    uint256 private constant SESSION_KEY = 0x515510;

    function testCompactRSVoucherNeedsNoRecoveryByte() public {
        address requester = vm.addr(REQUESTER_KEY);
        address sessionKey = vm.addr(SESSION_KEY);
        address provider = address(0xBEEF);
        address relayer = address(0xCAFE);

        WQPUToken token = new WQPUToken(1_000_000, requester);
        WQPURegistry registry = new WQPURegistry(uint128(1 ether));
        WQPUComputeMarket market = new WQPUComputeMarket(address(token), address(registry));

        vm.prank(requester);
        token.approve(address(market), type(uint256).max);
        vm.prank(requester);
        bytes32 channelId = market.openChannel(provider, 10 ether, uint64(block.timestamp + 1 days));

        bytes32 sessionId = keccak256("compact-session");
        uint128 maxAmount = uint128(5 ether);
        uint64 validUntil = uint64(block.timestamp + 2 hours);

        bytes32 authDigest = market.sessionAuthorizationDigest(
            requester, sessionKey, sessionId, maxAmount, validUntil
        );
        (uint8 authV, bytes32 authR, bytes32 authS) = vm.sign(REQUESTER_KEY, authDigest);
        bytes memory authorization = abi.encodePacked(authR, authS, authV);

        uint256 units = 1_000_000;
        uint256 amount = market.amountForUnits(channelId, units);
        bytes32 voucherDigest = market.voucherDigest(channelId, amount, units);
        (, bytes32 voucherR, bytes32 voucherS) = vm.sign(SESSION_KEY, voucherDigest);
        bytes memory compactVoucher = abi.encodePacked(voucherR, voucherS);

        vm.prank(relayer);
        market.claimWithSession(
            channelId,
            amount,
            units,
            compactVoucher,
            sessionKey,
            sessionId,
            maxAmount,
            validUntil,
            authorization
        );

        require(token.balanceOf(provider) == amount, "compact session payout failed");
        require(market.sessionSpent(requester, sessionId) == amount, "compact spend not tracked");
    }
}
