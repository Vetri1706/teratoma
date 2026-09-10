--------------------------- MODULE AegisDB_VSR ---------------------------
EXTENDS Naturals, Sequences, FiniteSets, TLC
CONSTANTS N, Values, MaxView, MaxLog, Mutation
VARIABLES nodes, sent, installed, floor, ack, known, decisions
vars == <<nodes,sent,installed,floor,ack,known,decisions>>
Replicas == 0..(N-1)
Views == 0..MaxView
Positions == 1..MaxLog
Entries == 1..((MaxView+1)*Values)
Quorum == (N \div 2)+1
Primary(v) == v % N
Prefix(s,k) == SubSeq(s,1,k)
Node(v,n,m,l,c) == [view |-> v, normal |-> n, status |-> m, log |-> l, commit |-> c]
Init ==
    /\ nodes = [r \in Replicas |-> Node(0,0,"Normal",<<>>,0)]
    /\ sent = [v \in Views |-> <<>>]
    /\ installed = [v \in Views |-> v=0]
    /\ floor = [v \in Views |-> 0]
    /\ ack = [v \in Views |-> [k \in Positions |-> {}]]
    /\ known = [v \in Views |-> 0]
    /\ decisions = <<>>
Crash(r) ==
    /\ nodes[r].status # "Recovery"
    /\ \A j \in Replicas : nodes[j].status # "Recovery"
    /\ nodes' = [nodes EXCEPT ![r].status = "Recovery"]
    /\ UNCHANGED <<sent,installed,floor,ack,known,decisions>>
Recover(r) ==
    /\ nodes[r].status = "Recovery"
    /\ Cardinality({j \in Replicas : nodes[j].status # "Recovery"}) >= Quorum
    /\ nodes' = [nodes EXCEPT ![r].status = IF nodes[r].view=nodes[r].normal THEN "Normal" ELSE "ViewChange"]
    /\ UNCHANGED <<sent,installed,floor,ack,known,decisions>>
StartViewChange(r,v) ==
    /\ nodes[r].status # "Recovery"
    /\ v > nodes[r].view
    /\ nodes' = [nodes EXCEPT ![r].view = v, ![r].status = "ViewChange"]
    /\ UNCHANGED <<sent,installed,floor,ack,known,decisions>>
Propose(r,value) ==
    LET v == nodes[r].view
        l == nodes[r].log
        k == Len(l)+1
    IN /\ nodes[r].status = "Normal"
       /\ r = Primary(v)
       /\ installed[v] /\ sent[v] = l
       /\ Len(l) < MaxLog
       /\ nodes' = [nodes EXCEPT ![r].log = Append(l,v*Values+value)]
       /\ sent' = [sent EXCEPT ![v] = Append(l,v*Values+value)]
       /\ ack' = [ack EXCEPT ![v][k] = @ \cup {r}]
       /\ UNCHANGED <<installed,floor,known,decisions>>
Commit(r) ==
    LET v == nodes[r].view
        k == nodes[r].commit+1
    IN /\ nodes[r].status = "Normal"
       /\ r = Primary(v)
       /\ installed[v] /\ sent[v] = nodes[r].log
       /\ k <= Len(nodes[r].log)
       /\ Cardinality(ack[v][k]) >= Quorum
       /\ nodes' = [nodes EXCEPT ![r].commit = k]
       /\ known' = [known EXCEPT ![v] = IF @ < k THEN k ELSE @]
       /\ decisions' = IF k > Len(decisions) THEN Append(decisions,<<nodes[r].log[k],v>>) ELSE decisions
       /\ UNCHANGED <<sent,installed,floor,ack>>
AckDelivery(v,r,k) ==
    [w \in Views |-> IF w=v THEN [j \in Positions |-> IF j<=k THEN ack[w][j] \cup {r} ELSE ack[w][j]] ELSE ack[w]]
PrepareOrStartView(v,r,k) ==
    LET old == nodes[r]
        received == Prefix(sent[v],k)
        replacement == Node(v,v,"Normal",received,old.commit)
    IN /\ installed[v]
       /\ old.status # "Recovery" /\ old.view <= v
       /\ floor[v] <= k /\ k <= Len(sent[v])
       /\ old.commit <= k
       /\ Prefix(received,old.commit) = Prefix(old.log,old.commit)
       /\ ((old.view=v /\ old.status="Normal") => (Len(old.log)<=k /\ Prefix(received,Len(old.log))=old.log))
       /\ (replacement # old \/ AckDelivery(v,r,k) # ack)
       /\ nodes' = [nodes EXCEPT ![r] = replacement]
       /\ ack' = AckDelivery(v,r,k)
       /\ UNCHANGED <<sent,installed,floor,known,decisions>>
CommitNotice(v,r) ==
    /\ installed[v]
    /\ nodes[r].status="Normal" /\ nodes[r].view=v
    /\ nodes[r].commit < known[v] /\ known[v] <= Len(nodes[r].log)
    /\ Prefix(nodes[r].log,known[v])=Prefix(sent[v],known[v])
    /\ nodes' = [nodes EXCEPT ![r].commit = known[v]]
    /\ UNCHANGED <<sent,installed,floor,ack,known,decisions>>
RankGe(i,j) ==
    \/ nodes[i].normal > nodes[j].normal
    \/ /\ nodes[i].normal = nodes[j].normal
       /\ Len(nodes[i].log) > Len(nodes[j].log)
    \/ /\ nodes[i].normal = nodes[j].normal
       /\ Len(nodes[i].log) = Len(nodes[j].log)
       /\ i <= j
Best(Q) == CHOOSE i \in Q : \A j \in Q : RankGe(i,j)
Worst(Q) == CHOOSE i \in Q : \A j \in Q : RankGe(j,i)
MaxCommit(Q) == CHOOSE k \in 0..MaxLog : (\E r \in Q : nodes[r].commit=k) /\ (\A r \in Q : nodes[r].commit<=k)
DoViewChange(v,Q) ==
    LET p == Primary(v)
        source == IF Mutation THEN Worst(Q) ELSE Best(Q)
        l == nodes[source].log
        c == IF Mutation /\ MaxCommit(Q)>Len(l) THEN Len(l) ELSE MaxCommit(Q)
    IN /\ v > 0 /\ ~installed[v]
       /\ Cardinality(Q)=Quorum /\ p \in Q
       /\ \A r \in Q : nodes[r].view=v /\ nodes[r].status="ViewChange"
       /\ nodes' = [nodes EXCEPT ![p] = Node(v,v,"Normal",l,c)]
       /\ sent' = [sent EXCEPT ![v] = l]
       /\ installed' = [installed EXCEPT ![v] = TRUE]
       /\ floor' = [floor EXCEPT ![v] = Len(l)]
       /\ ack' = AckDelivery(v,p,Len(l))
       /\ known' = [known EXCEPT ![v] = c]
       /\ UNCHANGED decisions
Next ==
    \/ \E r \in Replicas : Crash(r) \/ Recover(r) \/ Commit(r)
    \/ \E r \in Replicas, v \in Views : StartViewChange(r,v)
    \/ \E r \in Replicas, value \in 1..Values : Propose(r,value)
    \/ \E v \in Views, r \in Replicas, k \in 0..MaxLog : PrepareOrStartView(v,r,k)
    \/ \E v \in Views, r \in Replicas : CommitNotice(v,r)
    \/ \E v \in Views, Q \in SUBSET Replicas : DoViewChange(v,Q)
DecisionLog == [i \in 1..Len(decisions) |-> decisions[i][1]]
TypeOK ==
    /\ DOMAIN nodes = Replicas
    /\ \A r \in Replicas :
        /\ nodes[r].view \in Views
        /\ nodes[r].normal \in 0..nodes[r].view
        /\ nodes[r].status \in {"Normal","ViewChange","Recovery"}
        /\ nodes[r].log \in Seq(Entries)
        /\ Len(nodes[r].log)<=MaxLog
        /\ nodes[r].commit \in 0..Len(nodes[r].log)
Agreement ==
    \A r \in Replicas :
        /\ nodes[r].commit <= Len(decisions)
        /\ Prefix(nodes[r].log,nodes[r].commit)=Prefix(DecisionLog,nodes[r].commit)
LeaderCompleteness ==
    \A r \in Replicas :
        (nodes[r].status="Normal" /\ r=Primary(nodes[r].view)) =>
          (\A k \in 1..Len(decisions) : decisions[k][2]<nodes[r].view =>
             (k<=Len(nodes[r].log) /\ nodes[r].log[k]=decisions[k][1]))
LogMatching ==
    \A r,s \in Replicas :
      \A k \in 1..MaxLog :
        (k<=Len(nodes[r].log) /\ k<=Len(nodes[s].log)) =>
          (((nodes[r].log[k]-1) \div Values = (nodes[s].log[k]-1) \div Values) => Prefix(nodes[r].log,k)=Prefix(nodes[s].log,k))
Safety == TypeOK /\ Agreement /\ LeaderCompleteness /\ LogMatching
Spec == Init /\ [][Next]_vars
RecoveryFairSpec == Spec /\ (\A r \in Replicas : WF_vars(Recover(r)))
=============================================================================
