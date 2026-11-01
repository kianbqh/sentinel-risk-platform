package server

import (
	"context"
	"encoding/json"
	"fmt"
	"testing"
)

func TestSerialMutationAliasesSeeFreshHistoryAndLabels(t *testing.T) {
	ingest(t, "alias-lifecycle")
	token, _ := register(t, "aliases@example.com")
	var id int64
	if err := st.DB.QueryRow(context.Background(), `SELECT id FROM cases WHERE alert_id='alias-lifecycle'`).Scan(&id); err != nil {
		t.Fatal(err)
	}
	user := checked(t, gql(t, token, `{me{id}}`, nil))
	var me struct{ ID string }
	json.Unmarshal(user.Data["me"], &me)
	response := checked(t, gql(t, token, `mutation($id:ID!,$u:ID!){
 a:assignCase(id:$id,assigneeId:$u){status events{action} labels{value}}
 b:labelCase(id:$id,value:FRAUD,note:"first"){status events{action} labels{value}}
 c:labelCase(id:$id,value:BENIGN,note:"correction"){status events{action} labels{value}}
 d:resolveCase(id:$id,note:"done"){status events{action} labels{value}}
 }`, map[string]any{"id": fmt.Sprint(id), "u": me.ID}))
	for index, key := range []string{"a", "b", "c", "d"} {
		var row struct {
			Status string
			Events []struct{ Action string }
			Labels []struct{ Value string }
		}
		if err := json.Unmarshal(response.Data[key], &row); err != nil {
			t.Fatal(err)
		}
		if len(row.Events) != index+2 {
			t.Fatalf("alias %s used stale history: %s", key, response.Data[key])
		}
		if index == 0 {
			if len(row.Labels) != 0 {
				t.Fatal("premature label")
			}
		} else {
			expected := "BENIGN"
			if index == 1 {
				expected = "FRAUD"
			}
			if len(row.Labels) != 1 || row.Labels[0].Value != expected {
				t.Fatalf("alias %s used stale label: %s", key, response.Data[key])
			}
		}
		if index == 3 && row.Status != "RESOLVED" {
			t.Fatal("resolve alias did not resolve")
		}
	}
}
