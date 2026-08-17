// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

interface IWQPUToken {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

interface IWQPURegistryPrice {
    function globalPricePerMillionUnits() external view returns (uint128);
}

/// @title WQPU Compute Market
/// @notice Shared escrow + bounded payment sessions for permissionless compute providers.
contract WQPUComputeMarket {
    uint256 public constant CLAIM_GRACE = 1 days;
    uint256 public constant PRICE_UNITS = 1_000_000;

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant VOUCHER_TYPEHASH = keccak256(
        "Voucher(bytes32 channelId,uint256 cumulativeAmount,uint256 cumulativeUnits)"
    );
    bytes32 private constant SPEND_AUTH_TYPEHASH = keccak256(
        "SpendAuthorization(address requester,address sessionKey,bytes32 sessionId,uint128 maxAmount,uint128 pricePerMillionUnits,uint64 validUntil)"
    );
    bytes32 private constant PROVIDER_VOUCHER_TYPEHASH = keccak256(
        "ProviderVoucher(address requester,address provider,bytes32 sessionId,uint256 cumulativeAmount,uint256 cumulativeUnits)"
    );
    bytes32 private constant NAME_HASH = keccak256("WQPU Compute Market");
    bytes32 private constant VERSION_HASH = keccak256("1");

    uint256 private constant HALF_ORDER =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    IWQPUToken public immutable token;
    IWQPURegistryPrice public immutable registry;
    bytes32 public immutable DOMAIN_SEPARATOR;

    mapping(address => uint256) public nonces;
    mapping(address => uint128) public escrowBalance;
    mapping(address => uint128) public reservedEscrow;
    mapping(address => mapping(bytes32 => uint128)) public sessionSpent;
    mapping(address => mapping(bytes32 => bool)) public revokedSessions;
    mapping(address => mapping(bytes32 => mapping(address => uint128))) public sessionProviderPaid;
    mapping(address => mapping(bytes32 => mapping(address => uint128))) public sessionProviderUnits;

    struct Channel {
        address requester;
        address provider;
        uint128 deposited;
        uint128 paid;
        uint128 cumulativeUnits;
        uint128 pricePerMillionUnits;
        uint64 expiresAt;
        bool refunded;
    }

    struct SpendAuthorizationData {
        address requester;
        address sessionKey;
        bytes32 sessionId;
        uint128 maxAmount;
        uint128 pricePerMillionUnits;
        uint64 validUntil;
    }

    struct ProviderVoucherData {
        address provider;
        uint256 cumulativeAmount;
        uint256 cumulativeUnits;
    }

    struct ActiveSession {
        address sessionKey;
        uint128 maxAmount;
        uint128 pricePerMillionUnits;
        uint128 reservedRemaining;
        uint64 validUntil;
        bool active;
    }

    mapping(bytes32 => Channel) public channels;
    mapping(address => mapping(bytes32 => ActiveSession)) public activeSessions;

    event EscrowDeposited(address indexed requester, uint256 amount, uint256 newBalance);
    event EscrowWithdrawn(address indexed requester, uint256 amount, uint256 newBalance);
    event SessionActivated(
        address indexed requester,
        bytes32 indexed sessionId,
        address indexed sessionKey,
        uint256 maxAmount,
        uint256 pricePerMillionUnits,
        uint64 validUntil
    );
    event SessionReleased(
        address indexed requester,
        bytes32 indexed sessionId,
        uint256 releasedAmount
    );
    event EscrowSessionClaimed(
        bytes32 indexed sessionId,
        address indexed requester,
        address indexed provider,
        address sessionKey,
        uint256 paidNow,
        uint256 providerCumulativePaid,
        uint256 providerCumulativeUnits,
        uint256 sessionSpent
    );
    event ChannelOpened(
        bytes32 indexed channelId,
        address indexed requester,
        address indexed provider,
        uint256 deposit,
        uint128 pricePerMillionUnits,
        uint64 expiresAt
    );
    event ChannelToppedUp(bytes32 indexed channelId, uint256 amount, uint256 newDeposit);
    event VoucherClaimed(
        bytes32 indexed channelId,
        address indexed provider,
        uint256 paidNow,
        uint256 cumulativePaid,
        uint256 cumulativeUnits
    );
    event SessionRevoked(address indexed requester, bytes32 indexed sessionId);
    event ChannelRefunded(bytes32 indexed channelId, address indexed requester, uint256 amount);

    constructor(address tokenAddress, address registryAddress) {
        require(tokenAddress != address(0), "zero token");
        require(registryAddress != address(0), "zero registry");
        token = IWQPUToken(tokenAddress);
        registry = IWQPURegistryPrice(registryAddress);
        DOMAIN_SEPARATOR = keccak256(
            abi.encode(
                DOMAIN_TYPEHASH,
                NAME_HASH,
                VERSION_HASH,
                block.chainid,
                address(this)
            )
        );
    }

    /// @notice One shared requester balance can pay any provider selected later.
    function deposit(uint256 amount) external {
        require(amount != 0, "zero amount");
        uint256 next = uint256(escrowBalance[msg.sender]) + amount;
        require(next <= type(uint128).max, "escrow too large");
        require(token.transferFrom(msg.sender, address(this), amount), "deposit failed");
        escrowBalance[msg.sender] = uint128(next);
        emit EscrowDeposited(msg.sender, amount, next);
    }

    /// @notice Only escrow not reserved for active sessions can be withdrawn.
    function withdraw(uint256 amount) external {
        require(amount != 0, "zero amount");
        uint256 balance = escrowBalance[msg.sender];
        uint256 reserved = reservedEscrow[msg.sender];
        require(balance >= reserved, "bad reserve");
        require(amount <= balance - reserved, "escrow reserved");
        uint256 next = balance - amount;
        escrowBalance[msg.sender] = uint128(next);
        require(token.transfer(msg.sender, amount), "withdraw failed");
        emit EscrowWithdrawn(msg.sender, amount, next);
    }

    /// @notice Lock maxAmount from requester escrow behind one signed session authorization.
    /// @dev Anyone may relay activation; the requester wallet signature authorizes the lock.
    function activateSession(
        SpendAuthorizationData calldata auth,
        bytes calldata authorizationSignature
    ) external {
        require(auth.requester != address(0), "zero requester");
        require(auth.sessionKey != address(0), "zero session key");
        require(auth.maxAmount != 0, "zero session limit");
        require(auth.pricePerMillionUnits != 0, "zero session price");
        require(auth.validUntil > block.timestamp, "session expired");
        require(!revokedSessions[auth.requester][auth.sessionId], "session revoked");
        require(!activeSessions[auth.requester][auth.sessionId].active, "session active");
        require(sessionSpent[auth.requester][auth.sessionId] == 0, "session id used");
        require(
            registry.globalPricePerMillionUnits() == auth.pricePerMillionUnits,
            "network price changed"
        );

        bytes32 digest = spendAuthorizationDigest(
            auth.requester,
            auth.sessionKey,
            auth.sessionId,
            auth.maxAmount,
            auth.pricePerMillionUnits,
            auth.validUntil
        );
        require(_recover(digest, authorizationSignature) == auth.requester, "bad spend auth");

        uint256 balance = escrowBalance[auth.requester];
        uint256 reserved = reservedEscrow[auth.requester];
        require(balance >= reserved, "bad reserve");
        require(auth.maxAmount <= balance - reserved, "insufficient free escrow");

        activeSessions[auth.requester][auth.sessionId] = ActiveSession({
            sessionKey: auth.sessionKey,
            maxAmount: auth.maxAmount,
            pricePerMillionUnits: auth.pricePerMillionUnits,
            reservedRemaining: auth.maxAmount,
            validUntil: auth.validUntil,
            active: true
        });
        reservedEscrow[auth.requester] = uint128(reserved + auth.maxAmount);

        emit SessionActivated(
            auth.requester,
            auth.sessionId,
            auth.sessionKey,
            auth.maxAmount,
            auth.pricePerMillionUnits,
            auth.validUntil
        );
    }

    /// @notice Release unused reservation only after providers had an expiry + grace window to claim.
    function releaseSession(bytes32 sessionId) external {
        ActiveSession storage session = activeSessions[msg.sender][sessionId];
        require(session.active, "session inactive");
        require(
            block.timestamp > uint256(session.validUntil) + CLAIM_GRACE,
            "claim grace active"
        );
        uint128 remaining = session.reservedRemaining;
        session.reservedRemaining = 0;
        session.active = false;
        reservedEscrow[msg.sender] -= remaining;
        emit SessionReleased(msg.sender, sessionId, remaining);
    }

    /// @notice Anyone may relay a provider voucher signed by the activated local session key.
    function claimEscrowWithSession(
        address requester,
        bytes32 sessionId,
        ProviderVoucherData calldata voucher,
        bytes calldata voucherSignature
    ) external {
        ActiveSession storage session = activeSessions[requester][sessionId];
        require(session.active, "session inactive");
        require(
            block.timestamp <= uint256(session.validUntil) + CLAIM_GRACE,
            "session claim closed"
        );
        _validateProviderVoucher(requester, sessionId, session, voucher, voucherSignature);
        _settleEscrowSession(requester, sessionId, session, voucher);
    }

    /// @notice Revoke an authorization before it has been activated.
    /// @dev Active sessions deliberately cannot be cancelled early: providers must be able to
    ///      claim already-earned cumulative vouchers. Risk is bounded by maxAmount + validUntil.
    function revokeSession(bytes32 sessionId) external {
        require(!activeSessions[msg.sender][sessionId].active, "active session locked");
        revokedSessions[msg.sender][sessionId] = true;
        emit SessionRevoked(msg.sender, sessionId);
    }

    /// @notice Legacy provider-specific channel retained for direct wallet-signed vouchers.
    function openChannel(address provider, uint256 amount, uint64 expiresAt)
        external
        returns (bytes32 channelId)
    {
        require(provider != address(0) && provider != msg.sender, "bad provider");
        require(amount != 0 && amount <= type(uint128).max, "bad amount");
        require(expiresAt > block.timestamp, "bad expiry");

        uint128 price = registry.globalPricePerMillionUnits();
        require(price != 0, "zero network price");

        uint256 nonce = nonces[msg.sender]++;
        channelId = keccak256(
            abi.encode(block.chainid, address(this), msg.sender, provider, nonce)
        );
        require(channels[channelId].requester == address(0), "exists");
        require(token.transferFrom(msg.sender, address(this), amount), "deposit failed");

        channels[channelId] = Channel({
            requester: msg.sender,
            provider: provider,
            deposited: uint128(amount),
            paid: 0,
            cumulativeUnits: 0,
            pricePerMillionUnits: price,
            expiresAt: expiresAt,
            refunded: false
        });

        emit ChannelOpened(channelId, msg.sender, provider, amount, price, expiresAt);
    }

    function topUp(bytes32 channelId, uint256 amount) external {
        Channel storage channel = channels[channelId];
        require(channel.requester == msg.sender, "not requester");
        require(!channel.refunded, "refunded");
        require(block.timestamp <= channel.expiresAt, "expired");
        require(amount != 0, "zero amount");

        uint256 newDeposit = uint256(channel.deposited) + amount;
        require(newDeposit <= type(uint128).max, "deposit too large");
        require(token.transferFrom(msg.sender, address(this), amount), "top up failed");
        channel.deposited = uint128(newDeposit);
        emit ChannelToppedUp(channelId, amount, newDeposit);
    }

    function amountForUnits(bytes32 channelId, uint256 cumulativeUnits)
        public
        view
        returns (uint256)
    {
        Channel storage channel = channels[channelId];
        require(channel.requester != address(0), "unknown channel");
        return (cumulativeUnits * uint256(channel.pricePerMillionUnits)) / PRICE_UNITS;
    }

    function claim(
        bytes32 channelId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits,
        bytes calldata signature
    ) external {
        Channel storage channel = channels[channelId];
        require(channel.requester != address(0), "unknown channel");
        bytes32 digest = voucherDigest(channelId, cumulativeAmount, cumulativeUnits);
        require(_recover(digest, signature) == channel.requester, "bad voucher");
        _settle(channelId, cumulativeAmount, cumulativeUnits);
    }

    function refundExpired(bytes32 channelId) external {
        Channel storage channel = channels[channelId];
        require(channel.requester != address(0), "unknown channel");
        require(!channel.refunded, "already refunded");
        require(block.timestamp > uint256(channel.expiresAt) + CLAIM_GRACE, "claim grace active");

        channel.refunded = true;
        uint256 refund = uint256(channel.deposited) - uint256(channel.paid);
        if (refund != 0) {
            require(token.transfer(channel.requester, refund), "refund failed");
        }
        emit ChannelRefunded(channelId, channel.requester, refund);
    }

    function voucherDigest(
        bytes32 channelId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits
    ) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(VOUCHER_TYPEHASH, channelId, cumulativeAmount, cumulativeUnits)
        );
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    function spendAuthorizationDigest(
        address requester,
        address sessionKey,
        bytes32 sessionId,
        uint128 maxAmount,
        uint128 pricePerMillionUnits,
        uint64 validUntil
    ) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                SPEND_AUTH_TYPEHASH,
                requester,
                sessionKey,
                sessionId,
                maxAmount,
                pricePerMillionUnits,
                validUntil
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    function providerVoucherDigest(
        address requester,
        address provider,
        bytes32 sessionId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits
    ) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                PROVIDER_VOUCHER_TYPEHASH,
                requester,
                provider,
                sessionId,
                cumulativeAmount,
                cumulativeUnits
            )
        );
        return keccak256(abi.encodePacked("\x19\x01", DOMAIN_SEPARATOR, structHash));
    }

    function _validateProviderVoucher(
        address requester,
        bytes32 sessionId,
        ActiveSession storage session,
        ProviderVoucherData calldata voucher,
        bytes calldata voucherSignature
    ) internal view {
        require(voucher.provider != address(0) && voucher.provider != requester, "bad provider");
        require(voucher.cumulativeUnits <= type(uint128).max, "units too large");
        require(voucher.cumulativeAmount <= type(uint128).max, "amount too large");
        uint256 expectedAmount =
            (voucher.cumulativeUnits * uint256(session.pricePerMillionUnits)) / PRICE_UNITS;
        require(voucher.cumulativeAmount == expectedAmount, "wrong session price");

        bytes32 digest = providerVoucherDigest(
            requester,
            voucher.provider,
            sessionId,
            voucher.cumulativeAmount,
            voucher.cumulativeUnits
        );
        require(
            _sessionSignatureMatches(digest, voucherSignature, session.sessionKey),
            "bad provider voucher"
        );
    }

    function _settleEscrowSession(
        address requester,
        bytes32 sessionId,
        ActiveSession storage session,
        ProviderVoucherData calldata voucher
    ) internal {
        uint256 previousPaid = sessionProviderPaid[requester][sessionId][voucher.provider];
        uint256 previousUnits = sessionProviderUnits[requester][sessionId][voucher.provider];
        require(voucher.cumulativeAmount > previousPaid, "nothing new");
        require(voucher.cumulativeUnits >= previousUnits, "units decreased");

        uint256 delta = voucher.cumulativeAmount - previousPaid;
        uint256 spent = uint256(sessionSpent[requester][sessionId]) + delta;
        require(spent <= session.maxAmount, "session limit");
        require(delta <= session.reservedRemaining, "session reserve");
        require(delta <= escrowBalance[requester], "escrow balance");

        sessionProviderPaid[requester][sessionId][voucher.provider] =
            uint128(voucher.cumulativeAmount);
        sessionProviderUnits[requester][sessionId][voucher.provider] =
            uint128(voucher.cumulativeUnits);
        sessionSpent[requester][sessionId] = uint128(spent);
        session.reservedRemaining -= uint128(delta);
        reservedEscrow[requester] -= uint128(delta);
        escrowBalance[requester] -= uint128(delta);

        require(token.transfer(voucher.provider, delta), "provider payment failed");
        emit EscrowSessionClaimed(
            sessionId,
            requester,
            voucher.provider,
            session.sessionKey,
            delta,
            voucher.cumulativeAmount,
            voucher.cumulativeUnits,
            spent
        );
    }

    function _settle(
        bytes32 channelId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits
    ) internal returns (uint256 delta) {
        Channel storage channel = channels[channelId];
        require(channel.provider != address(0), "unknown channel");
        require(!channel.refunded, "refunded");
        require(block.timestamp <= uint256(channel.expiresAt) + CLAIM_GRACE, "claim closed");
        require(cumulativeUnits >= channel.cumulativeUnits, "units decreased");
        require(cumulativeUnits <= type(uint128).max, "units too large");
        require(cumulativeAmount == amountForUnits(channelId, cumulativeUnits), "wrong network price");
        require(cumulativeAmount <= channel.deposited, "over deposit");
        require(cumulativeAmount > channel.paid, "nothing new");

        delta = cumulativeAmount - channel.paid;
        channel.paid = uint128(cumulativeAmount);
        channel.cumulativeUnits = uint128(cumulativeUnits);

        require(token.transfer(channel.provider, delta), "payment failed");
        emit VoucherClaimed(
            channelId,
            channel.provider,
            delta,
            cumulativeAmount,
            cumulativeUnits
        );
    }

    function _sessionSignatureMatches(
        bytes32 digest,
        bytes calldata signature,
        address expected
    ) internal pure returns (bool) {
        require(signature.length == 64, "bad session signature length");
        bytes32 r;
        bytes32 s;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
        }
        require(uint256(s) <= HALF_ORDER, "bad signature s");
        address first = ecrecover(digest, 27, r, s);
        if (first == expected) return true;
        return ecrecover(digest, 28, r, s) == expected;
    }

    function _recover(bytes32 digest, bytes calldata signature) internal pure returns (address) {
        require(signature.length == 65, "bad signature length");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(signature.offset)
            s := calldataload(add(signature.offset, 32))
            v := byte(0, calldataload(add(signature.offset, 64)))
        }
        require(uint256(s) <= HALF_ORDER, "bad signature s");
        require(v == 27 || v == 28, "bad signature v");
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0), "bad signer");
        return signer;
    }
}
