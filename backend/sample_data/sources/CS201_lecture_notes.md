# CS201 Data Structures - Lecture Notes

## Unit 1: Algorithm Analysis

Big-O notation describes how the running time of an algorithm grows as the input size n grows, ignoring constant factors. A single loop over n items is O(n). Two nested loops over n items are O(n^2). Binary search halves the search range at every step, so it is O(log n). Accessing an array element by index is O(1), constant time.

When adding costs, keep the dominant term: O(n^2 + n) simplifies to O(n^2).

## Unit 2: Linear Structures

### Stacks and queues

A stack is last-in, first-out (LIFO): `push` adds to the top and `pop` removes from the top. Stacks are used for undo features, matching brackets and function calls. A queue is first-in, first-out (FIFO): `enqueue` adds at the back and `dequeue` removes from the front. Queues are used for scheduling and breadth-first search. Both operations are O(1) when implemented well.

### Arrays and linked lists

An array stores elements in contiguous memory, so access by index is O(1), but inserting at the front is O(n) because every element must shift. A singly linked list stores nodes that each point to the next node; inserting at the head is O(1) but accessing the k-th element is O(k) because you must follow the links from the head.

## Unit 3: Trees

A binary search tree (BST) stores keys so that every key in a node's left subtree is smaller and every key in its right subtree is larger. In-order traversal (left, node, right) visits keys in sorted order. Pre-order traversal (node, left, right) visits the root first. Post-order traversal (left, right, node) visits the root last.

For the BST with root 8, left child 3 and right child 10, the in-order traversal is 3, 8, 10 and the pre-order traversal is 8, 3, 10.
