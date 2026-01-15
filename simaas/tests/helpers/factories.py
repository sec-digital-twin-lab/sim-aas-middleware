"""Test data factories for creating test objects.

This module provides builder patterns and factory functions for creating
Task objects and other test data consistently across tests.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union

from simaas.core.keystore import Keystore
from simaas.nodedb.schemas import ResourceDescriptor
from simaas.rti.schemas import Task


@dataclass
class TaskBuilder:
    """
    Builder pattern for creating Task objects.

    Example:
        task = (TaskBuilder(proc_id, owner.identity.id)
                .with_input_value('a', {'v': 1})
                .with_input_value('b', {'v': 2})
                .with_output('c', owner.identity.id)
                .build())
    """
    proc_id: str
    user_iid: str
    name: Optional[str] = None
    description: Optional[str] = None
    budget_vcpus: int = 1
    budget_memory: int = 1024
    namespace: Optional[str] = None
    inputs: List[Union[Task.InputValue, Task.InputReference]] = field(default_factory=list)
    outputs: List[Task.Output] = field(default_factory=list)

    def with_name(self, name: str) -> 'TaskBuilder':
        """Set the task name."""
        self.name = name
        return self

    def with_description(self, description: str) -> 'TaskBuilder':
        """Set the task description."""
        self.description = description
        return self

    def with_budget(self, vcpus: int = 1, memory: int = 1024) -> 'TaskBuilder':
        """Set the resource budget for the task."""
        self.budget_vcpus = vcpus
        self.budget_memory = memory
        return self

    def with_namespace(self, namespace: str) -> 'TaskBuilder':
        """Set the namespace for the task."""
        self.namespace = namespace
        return self

    def with_input_value(self, name: str, value: dict) -> 'TaskBuilder':
        """
        Add a by-value input to the task.

        Args:
            name: The input name (must match processor definition).
            value: The JSON value for this input.
        """
        self.inputs.append(Task.InputValue(name=name, type='value', value=value))
        return self

    def with_input_reference(
        self,
        name: str,
        obj_id: str,
        user_signature: Optional[str] = None,
        c_hash: Optional[str] = None
    ) -> 'TaskBuilder':
        """
        Add a by-reference input to the task.

        Args:
            name: The input name (must match processor definition).
            obj_id: The ID of the data object to reference.
            user_signature: Optional signature for restricted access objects.
            c_hash: Optional content hash of the input.
        """
        self.inputs.append(Task.InputReference(
            name=name,
            type='reference',
            obj_id=obj_id,
            user_signature=user_signature,
            c_hash=c_hash
        ))
        return self

    def with_output(
        self,
        name: str,
        owner_iid: str,
        restricted_access: bool = False,
        content_encrypted: bool = False,
        target_node_iid: Optional[str] = None
    ) -> 'TaskBuilder':
        """
        Add an output definition to the task.

        Args:
            name: The output name (must match processor definition).
            owner_iid: The identity ID of the output owner.
            restricted_access: Whether access to the output should be restricted.
            content_encrypted: Whether the output should be encrypted.
            target_node_iid: Optional target node for storing the output.
        """
        self.outputs.append(Task.Output(
            name=name,
            owner_iid=owner_iid,
            restricted_access=restricted_access,
            content_encrypted=content_encrypted,
            target_node_iid=target_node_iid
        ))
        return self

    def build(self) -> Task:
        """Build and return the Task object."""
        return Task(
            proc_id=self.proc_id,
            user_iid=self.user_iid,
            input=self.inputs,
            output=self.outputs,
            name=self.name,
            description=self.description,
            budget=ResourceDescriptor(vcpus=self.budget_vcpus, memory=self.budget_memory),
            namespace=self.namespace
        )


def create_abc_task(
    proc_id: str,
    owner: Keystore,
    a: int = 1,
    b: int = 1,
    memory: int = 1024,
    namespace: Optional[str] = None
) -> Task:
    """
    Factory function for creating ABC processor tasks.

    The ABC processor takes two integer inputs 'a' and 'b' and produces
    an output 'c' = a + b.

    Args:
        proc_id: The processor ID (data object ID of the deployed processor).
        owner: The keystore of the task owner.
        a: The first input value (default: 1).
        b: The second input value (default: 1).
        memory: Memory budget in MB (default: 1024).
        namespace: Optional namespace for the task.

    Returns:
        A Task object configured for the ABC processor.
    """
    builder = (TaskBuilder(proc_id, owner.identity.id)
               .with_input_value('a', {'v': a})
               .with_input_value('b', {'v': b})
               .with_output('c', owner.identity.id)
               .with_budget(memory=memory))

    if namespace:
        builder.with_namespace(namespace)

    return builder.build()


def create_ping_task(
    proc_id: str,
    owner: Keystore,
    message: str = "ping",
    memory: int = 1024,
    namespace: Optional[str] = None
) -> Task:
    """
    Factory function for creating Ping processor tasks.

    The Ping processor takes a message input and echoes it back.

    Args:
        proc_id: The processor ID (data object ID of the deployed processor).
        owner: The keystore of the task owner.
        message: The message to ping (default: "ping").
        memory: Memory budget in MB (default: 1024).
        namespace: Optional namespace for the task.

    Returns:
        A Task object configured for the Ping processor.
    """
    builder = (TaskBuilder(proc_id, owner.identity.id)
               .with_input_value('message', {'content': message})
               .with_output('response', owner.identity.id)
               .with_budget(memory=memory))

    if namespace:
        builder.with_namespace(namespace)

    return builder.build()
