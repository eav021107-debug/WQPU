package precompile

import "testing"

func TestProviderPublishV2BindsConcretePeerToSession(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	if err := CommitProviderPublishV2(state, envelope, DevNetworkConfig, 121); err != nil {
		t.Fatal(err)
	}
	session, exists, err := LoadPeerControlSession(state, envelope.Announcement.PeerID)
	if err != nil || !exists || session != delegation.Session {
		t.Fatalf("session=%s exists=%v err=%v", session.Hex(), exists, err)
	}
}

func TestUnknownPeerHasNoControlSession(t *testing.T) {
	state := newMemoryState()
	_, exists, err := LoadPeerControlSession(state, validProvider().PeerID)
	if err != nil || exists {
		t.Fatalf("exists=%v err=%v", exists, err)
	}
}
