// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title WQPU Registry
/// @notice Permissionless on-chain directory of active WQPU compute nodes.
/// @dev Nodes publish reachability/capacity/load/TLS identity. Price is global for the network.
contract WQPURegistry {
    uint16 public constant BPS = 10_000;

    struct Node {
        string endpoint;
        bytes32 tlsFingerprint;
        uint64 capacity;
        uint16 loadBps;
        uint64 updatedAt;
        bool active;
    }

    mapping(address => Node) public nodes;
    mapping(address => bool) private known;
    address[] private members;

    uint128 public globalPricePerMillionUnits;
    address public priceController;

    event NodeAnnounced(
        address indexed wallet,
        string endpoint,
        bytes32 tlsFingerprint,
        uint64 capacity,
        uint16 loadBps,
        uint64 updatedAt
    );
    event NodeLoadUpdated(address indexed wallet, uint16 loadBps, uint64 updatedAt);
    event NodeOffline(address indexed wallet, uint64 updatedAt);
    event GlobalPriceUpdated(uint128 oldPrice, uint128 newPrice);
    event PriceControllerTransferred(address indexed oldController, address indexed newController);

    constructor(uint128 initialPricePerMillionUnits) {
        require(initialPricePerMillionUnits != 0, "zero price");
        globalPricePerMillionUnits = initialPricePerMillionUnits;
        priceController = msg.sender;
    }

    /// @notice Register/update a node. The caller wallet is the node identity.
    function announce(
        string calldata endpoint,
        bytes32 tlsFingerprint,
        uint64 capacity,
        uint16 loadBps
    ) external {
        require(bytes(endpoint).length != 0, "empty endpoint");
        require(tlsFingerprint != bytes32(0), "empty fingerprint");
        require(capacity != 0, "zero capacity");
        require(loadBps <= BPS, "bad load");

        if (!known[msg.sender]) {
            known[msg.sender] = true;
            members.push(msg.sender);
        }

        nodes[msg.sender] = Node({
            endpoint: endpoint,
            tlsFingerprint: tlsFingerprint,
            capacity: capacity,
            loadBps: loadBps,
            updatedAt: uint64(block.timestamp),
            active: true
        });

        emit NodeAnnounced(
            msg.sender,
            endpoint,
            tlsFingerprint,
            capacity,
            loadBps,
            uint64(block.timestamp)
        );
    }

    /// @notice Cheap heartbeat/load update without rewriting endpoint/capacity.
    function updateLoad(uint16 loadBps) external {
        require(loadBps <= BPS, "bad load");
        Node storage node = nodes[msg.sender];
        require(known[msg.sender] && node.active, "inactive node");
        node.loadBps = loadBps;
        node.updatedAt = uint64(block.timestamp);
        emit NodeLoadUpdated(msg.sender, loadBps, uint64(block.timestamp));
    }

    function setOffline() external {
        Node storage node = nodes[msg.sender];
        require(known[msg.sender], "unknown node");
        node.active = false;
        node.updatedAt = uint64(block.timestamp);
        emit NodeOffline(msg.sender, uint64(block.timestamp));
    }

    /// @dev Prototype controller. On the WQPU chain this should be handed to chain governance.
    function setGlobalPrice(uint128 newPrice) external {
        require(msg.sender == priceController, "not price controller");
        require(newPrice != 0, "zero price");
        uint128 oldPrice = globalPricePerMillionUnits;
        globalPricePerMillionUnits = newPrice;
        emit GlobalPriceUpdated(oldPrice, newPrice);
    }

    function transferPriceController(address newController) external {
        require(msg.sender == priceController, "not price controller");
        require(newController != address(0), "zero controller");
        address oldController = priceController;
        priceController = newController;
        emit PriceControllerTransferred(oldController, newController);
    }

    function memberCount() external view returns (uint256) {
        return members.length;
    }

    function memberAt(uint256 index) external view returns (address wallet, Node memory node) {
        wallet = members[index];
        node = nodes[wallet];
    }

    function getMembers(uint256 offset, uint256 limit)
        external
        view
        returns (address[] memory wallets, Node[] memory entries)
    {
        if (offset >= members.length || limit == 0) {
            return (new address[](0), new Node[](0));
        }

        uint256 end = offset + limit;
        if (end > members.length) end = members.length;
        uint256 length = end - offset;

        wallets = new address[](length);
        entries = new Node[](length);
        for (uint256 i = 0; i < length; i++) {
            address wallet = members[offset + i];
            wallets[i] = wallet;
            entries[i] = nodes[wallet];
        }
    }

    /// @notice Deterministic score helper for requester-side scheduling.
    /// @dev Lower is better. Offline nodes always sort last.
    function schedulingScore(address wallet) external view returns (uint256) {
        Node storage node = nodes[wallet];
        if (!node.active || node.capacity == 0) return type(uint256).max;
        return (uint256(node.loadBps) * 1e18) / uint256(node.capacity);
    }
}
