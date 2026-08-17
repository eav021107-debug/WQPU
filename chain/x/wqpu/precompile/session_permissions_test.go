package precompile

import (
	"testing"

	legacykernel "github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func TestSessionPermissionBitsMatchReferenceKernel(t *testing.T) {
	if SessionPermProvider != legacykernel.SessionPermProvider {
		t.Fatalf("provider permission bit=%d reference=%d", SessionPermProvider, legacykernel.SessionPermProvider)
	}
	if SessionPermJob != legacykernel.SessionPermJob {
		t.Fatalf("job permission bit=%d reference=%d", SessionPermJob, legacykernel.SessionPermJob)
	}
	if SessionPermSettle != legacykernel.SessionPermSettle {
		t.Fatalf("settle permission bit=%d reference=%d", SessionPermSettle, legacykernel.SessionPermSettle)
	}
	if SessionAllPermissions != legacykernel.SessionAllPermissions {
		t.Fatalf("permission mask=%d reference=%d", SessionAllPermissions, legacykernel.SessionAllPermissions)
	}
}
