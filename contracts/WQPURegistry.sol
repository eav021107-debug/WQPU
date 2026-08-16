// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title WQPU Registry
/// @notice Permissionless on-chain directory of WQPU nodes.
/// @dev The registry does not coordinate compute. It only publishes how peers can be reached.
contract WQPURegistry {
    struct Node {
        string endpoint;
        uint128 pricePerMillionUnits;
        uint64 capacity;
        uint64 updatedAt;
        bool active;
    }

    mapping(address => Node) public nodes;
    mapping(address => bool) private known;
    address[] private members;

    event NodeAnnounced(
        address indexed wallet,
        string endpoint,
        uint128 pricePerMillionUnits,
        uint64 capacity,
        uint64 updatedAt
    );
    event NodeOffline(address indexed wallet, uint64 updatedAt);

    function announce(
        string calldata endpoint,
        uint128 pricePerMillionUnits,
        uint64 capacity
    ) external {
        require(bytes(endpoint).length != 0, "empty endpoint");
        require(capacity != 0, "zero capacity");

        if (!known[msg.sender]) {
            known[msg.sender] = true;
            members.push(msg.sender);
        }

        nodes[msg.sender] = Node({
            endpoint: endpoint,
            pricePerMillionUnits: pricePerMillionUnits,
            capacity: capacity,
            updatedAt: uint64(block.timestamp),
            active: true
        });

        emit NodeAnnounced(
            msg.sender,
            endpoint,
            pricePerMillionUnits,
            capacity,
            uint64(block.timestamp)
        );
    }

    function setOffline() external {
        Node storage node = nodes[msg.sender];
        require(known[msg.sender], "unknown node");
        node.active = false;
        node.updatedAt = uint64(block.timestamp);
        emit NodeOffline(msg.sender, uint64(block.timestamp));
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
}
