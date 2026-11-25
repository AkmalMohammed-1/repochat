package worker

import "errors"

// Job is a unit of background work, e.g. sending a receipt email.
type Job struct {
	ID      int
	Kind    string
	Payload string
}

// Queue is an in-memory FIFO of jobs.
type Queue struct {
	jobs []Job
}

// Enqueue adds a job to the back of the queue.
func (q *Queue) Enqueue(j Job) {
	q.jobs = append(q.jobs, j)
}

// Pop removes and returns the oldest job, or an error if the queue is empty.
func (q *Queue) Pop() (Job, error) {
	if len(q.jobs) == 0 {
		return Job{}, errors.New("queue is empty")
	}
	j := q.jobs[0]
	q.jobs = q.jobs[1:]
	return j, nil
}
