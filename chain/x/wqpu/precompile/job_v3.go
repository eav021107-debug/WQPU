package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func CommitJobReservationV3(state WordState, request JobRequest, requesterSession common.Address, height uint64) (JobReservation, error) {
	session, exists, err := LoadSession(state, request.RequesterWallet, requesterSession)
	if err != nil { return JobReservation{}, err }
	if !exists { return JobReservation{}, errors.New("unknown WQPU requester session") }
	escrow, err := SessionEscrowUnits(state, request.RequesterWallet, requesterSession)
	if err != nil { return JobReservation{}, err }
	if session.ReservedUnits > escrow || request.MaxChargeUnits > escrow-session.ReservedUnits {
		return JobReservation{}, errors.New("WQPU job is not backed by funded native escrow")
	}
	return CommitJobReservationV2(state, request, requesterSession, height)
}

func CommitSignedJobReservationV3(state WordState, envelope JobReserveEnvelope, config NetworkConfig, height uint64) (JobReservation, error) {
	action, err := VerifyJobReserve(state, envelope, config, height)
	if err != nil { return JobReservation{}, err }
	job, err := CommitJobReservationV3(state, envelope.Request, envelope.Session, height)
	if err != nil { return JobReservation{}, err }
	if err := AdvanceSessionActionNonce(state, envelope.Wallet, envelope.Session, action.ActionNonce); err != nil { return JobReservation{}, err }
	return job, nil
}
