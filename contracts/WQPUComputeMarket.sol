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
/// @notice Escrow + bounded payment sessions for permissionless compute providers.
contract WQPUComputeMarket {
    uint256 public constant CLAIM_GRACE = 1 days;
    uint256 public constant PRICE_UNITS = 1_000_000;

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant VOUCHER_TYPEHASH = keccak256(
        "Voucher(bytes32 channelId,uint256 cumulativeAmount,uint256 cumulativeUnits)"
    );
    bytes32 private constant SESSION_TYPEHASH = keccak256(
        "SessionAuthorization(address requester,address sessionKey,bytes32 sessionId,uint128 maxAmount,uint64 validUntil)"
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

    mapping(bytes32 => Channel) public channels;

    event EscrowDeposited(address indexed requester, uint256 amount, uint256 newBalance);
    event EscrowWithdrawn(address indexed requester, uint256 amount, uint256 newBalance);
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
    event SessionClaimed(
        bytes32 indexed sessionId,
        address indexed requester,
        address indexed sessionKey,
        uint256 paidNow,
        uint256 sessionSpent
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

    function withdraw(uint256 amount) external {
        require(amount != 0, "zero amount");
        uint256 balance = escrowBalance[msg.sender];
        require(amount <= balance, "escrow balance");
        uint256 next = balance - amount;
        escrowBalance[msg.sender] = uint128(next);
        require(token.transfer(msg.sender, amount), "withdraw failed");
        emit EscrowWithdrawn(msg.sender, amount, next);
    }

    /// @notice Anyone may relay a provider voucher signed by a bounded local session key.
    /// @dev A single SpendAuthorization can pay many different providers from requester escrow.
    ///      The exact global price is included in wallet authorization; if network price changes,
    ///      a new authorization is required rather than silently charging a different price.
    function claimEscrowWithSession(
        address requester,
        address provider,
        bytes32 sessionId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits,
        bytes calldata voucherSignature,
        address sessionKey,
        uint128 maxAmount,
        uint128 pricePerMillionUnits,
        uint64 validUntil,
        bytes calldata authorizationSignature
    ) external {
        require(requester != address(0), "zero requester");
        require(provider != address(0) && provider != requester, "bad provider");
        require(sessionKey != address(0), "zero session key");
        require(maxAmount != 0, "zero session limit");
        require(pricePerMillionUnits != 0, "zero session price");
        require(block.timestamp <= validUntil, "session expired");
        require(!revokedSessions[requester][sessionId], "session revoked");
        require(registry.globalPricePerMillionUnits() == pricePerMillionUnits, "network price changed");

        bytes32 authDigest = spendAuthorizationDigest(
            requester,
            sessionKey,
            sessionId,
            maxAmount,
            pricePerMillionUnits,
            validUntil
        );
        require(_recover(authDigest, authorizationSignature) == requester, "bad spend auth");

        bytes32 voucher = providerVoucherDigest(
            requester,
            provider,
            sessionId,
            cumulativeAmount,
            cumulativeUnits
        );
        require(_sessionSignatureMatches(voucher, voucherSignature, sessionKey), "bad provider voucher");

        require(cumulativeUnits <= type(uint128).max, "units too large");
        require(cumulativeAmount <= type(uint128).max, "amount too large");
        uint256 expectedAmount = (cumulativeUnits * uint256(pricePerMillionUnits)) / PRICE_UNITS;
        require(cumulativeAmount == expectedAmount, "wrong network price");

        uint256 previousPaid = sessionProviderPaid[requester][sessionId][provider];
        uint256 previousUnits = sessionProviderUnits[requester][sessionId][provider];
        require(cumulativeAmount > previousPaid, "nothing new");
        require(cumulativeUnits >= previousUnits, "units decreased");
        uint256 delta = cumulativeAmount - previousPaid;

        uint256 spent = uint256(sessionSpent[requester][sessionId]) + delta;
        require(spent <= maxAmount, "session limit");
        require(delta <= escrowBalance[requester], "escrow balance");

        sessionProviderPaid[requester][sessionId][provider] = uint128(cumulativeAmount);
        sessionProviderUnits[requester][sessionId][provider] = uint128(cumulativeUnits);
        sessionSpent[requester][sessionId] = uint128(spent);
        escrowBalance[requester] -= uint128(delta);

        require(token.transfer(provider, delta), "provider payment failed");
        emit EscrowSessionClaimed(
            sessionId,
            requester,
            provider,
            sessionKey,
            delta,
            cumulativeAmount,
            cumulativeUnits,
            spent
        );
    }

    /// @notice Legacy/provider-specific channel path kept for compatibility during migration.
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

    function claimWithSession(
        bytes32 channelId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits,
        bytes calldata voucherSignature,
        address sessionKey,
        bytes32 sessionId,
        uint128 maxAmount,
        uint64 validUntil,
        bytes calldata authorizationSignature
    ) external {
        Channel storage channel = channels[channelId];
        require(channel.requester != address(0), "unknown channel");
        require(sessionKey != address(0), "zero session key");
        require(maxAmount != 0, "zero session limit");
        require(block.timestamp <= validUntil, "session expired");
        require(!revokedSessions[channel.requester][sessionId], "session revoked");

        bytes32 authDigest = sessionAuthorizationDigest(
            channel.requester,
            sessionKey,
            sessionId,
            maxAmount,
            validUntil
        );
        require(_recover(authDigest, authorizationSignature) == channel.requester, "bad session auth");

        bytes32 voucher = voucherDigest(channelId, cumulativeAmount, cumulativeUnits);
        require(_sessionSignatureMatches(voucher, voucherSignature, sessionKey), "bad session voucher");

        uint256 previousPaid = channel.paid;
        uint256 delta = _settle(channelId, cumulativeAmount, cumulativeUnits);
        require(delta == cumulativeAmount - previousPaid, "bad delta");

        uint256 spent = uint256(sessionSpent[channel.requester][sessionId]) + delta;
        require(spent <= maxAmount, "session limit");
        require(spent <= type(uint128).max, "session spend overflow");
        sessionSpent[channel.requester][sessionId] = uint128(spent);

        emit SessionClaimed(sessionId, channel.requester, sessionKey, delta, spent);
    }

    function revokeSession(bytes32 sessionId) external {
        revokedSessions[msg.sender][sessionId] = true;
        emit SessionRevoked(msg.sender, sessionId);
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

    function sessionAuthorizationDigest(
        address requester,
        address sessionKey,
        bytes32 sessionId,
        uint128 maxAmount,
        uint64 validUntil
    ) public view returns (bytes32) {
        bytes32 structHash = keccak256(
            abi.encode(
                SESSION_TYPEHASH,
                requester,
                sessionKey,
                sessionId,
                maxAmount,
                validUntil
            )
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
        if (signature.length == 65) {
            return _recover(digest, signature) == expected;
        }
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
        address second = ecrecover(digest, 28, r, s);
        return second == expected;
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
