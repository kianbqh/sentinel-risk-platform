package graph

import (
	"errors"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/auth"
	"github.com/kianbqh/sentinel-risk-platform/console/backend/internal/store"
	"strconv"
)

// Resolver is the root dependency container for gqlgen resolvers.
type Resolver struct {
	Store *store.Store
	Auth  auth.Manager
}

func validID(id string) error {
	v, err := strconv.ParseInt(id, 10, 64)
	if err != nil || v <= 0 {
		return errors.New("invalid case or analyst id")
	}
	return nil
}
