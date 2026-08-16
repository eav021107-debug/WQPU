// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

interface IWQPUToken {
    function transfer(address to, uint256 value) external returns (bool);
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

/// @title WQPU Compute Market
/// @notice Escrowed payment channels for permissionless compute providers.
/// @dev Providers do not mint tokens. Requesters deposit existing WQPU and sign cumulative vouchers.
contract WQPUComputeMarket {
    uint256 public constant CLAIM_GRACE = 1 days;

    bytes32 private constant DOMAIN_TYPEHASH = keccak256(
        "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
    );
    bytes32 private constant VOUCHER_TYPEHASH = keccak256(
        "Voucher(bytes32 channelId,uint256 cumulativeAmount,uint256 cumulativeUnits)"
    );
    bytes32 private constant NAME_HASH = keccak256("WQPU Compute Market");
    bytes32 private constant VERSION_HASH = keccak256("1");

    // secp256k1n / 2, used to reject malleable signatures.
    uint256 private constant HALF_ORDER =
        0x7fffffffffffffffffffffffffffffff5d576e7357a4501ddfe92f46681b20a0;

    IWQPUToken public immutable token;
    bytes32 public immutable DOMAIN_SEPARATOR;

    mapping(address => uint256) public nonces;

    struct Channel {
        address requester;
        address provider;
        uint128 deposited;
        uint128 paid;
        uint128 cumulativeUnits;
        uint64 expiresAt;
        bool refunded;
    }

    mapping(bytes32 => Channel) public channels;

    event ChannelOpened(
        bytes32 indexed channelId,
        address indexed requester,
        address indexed provider,
        uint256 deposit,
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
    event ChannelRefunded(bytes32 indexed channelId, address indexed requester, uint256 amount);

    constructor(address tokenAddress) {
        require(tokenAddress != address(0), "zero token");
        token = IWQPUToken(tokenAddress);
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

    function openChannel(address provider, uint256 amount, uint64 expiresAt)
        external
        returns (bytes32 channelId)
    {
        require(provider != address(0) && provider != msg.sender, "bad provider");
        require(amount != 0 && amount <= type(uint128).max, "bad amount");
        require(expiresAt > block.timestamp, "bad expiry");

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
            expiresAt: expiresAt,
            refunded: false
        });

        emit ChannelOpened(channelId, msg.sender, provider, amount, expiresAt);
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

    /// @notice Claim the newest cumulative voucher signed by the requester.
    /// @param cumulativeAmount Total WQPU owed to the provider so far.
    /// @param cumulativeUnits Total accepted compute units served so far.
    function claim(
        bytes32 channelId,
        uint256 cumulativeAmount,
        uint256 cumulativeUnits,
        bytes calldata signature
    ) external {
        Channel storage channel = channels[channelId];
        require(channel.provider == msg.sender, "not provider");
        require(!channel.refunded, "refunded");
        require(block.timestamp <= uint256(channel.expiresAt) + CLAIM_GRACE, "claim closed");
        require(cumulativeAmount <= channel.deposited, "over deposit");
        require(cumulativeAmount > channel.paid, "nothing new");
        require(cumulativeUnits >= channel.cumulativeUnits, "units decreased");
        require(cumulativeUnits <= type(uint128).max, "units too large");

        bytes32 digest = voucherDigest(channelId, cumulativeAmount, cumulativeUnits);
        require(_recover(digest, signature) == channel.requester, "bad voucher");

        uint256 delta = cumulativeAmount - channel.paid;
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
