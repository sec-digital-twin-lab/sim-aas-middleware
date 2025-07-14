# User Story Requirements

## Non-Admin User
- As a non-admin user, I want to use the application that is not exposed on the internet so that I can work with sensitive data in a secure, isolated environment
- As a non-admin user, I want to use the application on MacOS and Linux devices so that I can work on my preferred operating system without compatibility issues

### Non-Admin User - Manage Identities
- As a non-admin user, I want to update my profile so that I can keep my contact information and preferences current

## Non-Admin User - Manage Data Object Repository (DOR)
- As a non-admin user, I want to add a Data Object into the DOR so that I can store and share my simulation data with other users in the network
- As a non-admin user, I want to remove a Data Object from the DOR so that I can clean up outdated or incorrect data and manage storage space
- As a non-admin user, I want to grant access to a Data Object from the DOR so that I can collaborate with specific users while maintaining control over my data
- As a non-admin user, I want to revoke access to a Data Object from the DOR so that I can remove access for users who no longer need it or should not have access

## Non-Admin User - Manage Processor
- As a non-admin user, I want to build processor image from a local source so that I can use computational models from my local development environment
- As a non-admin user, I want to build processor image from a Github source so that I can use computational models from version-controlled repositories

## Non-Admin User - Job
- As a non-admin user, I want to submit a job so that I can execute my computational models and simulations on the distributed infrastructure
- As a non-admin user, I want to review results from a job so that I can analyze the output and make decisions based on the simulation results

---

## Admin User
- As a admin user, I want to use the application that is not exposed on the internet so that I can work with sensitive data in a secure, isolated environment
- As a admin user,I want to use the application on MacOS and Linux devices so that I can work on my preferred operating system without compatibility issues

### Admin User - Manage Identities
- As a admin user, I want to create new keystore/identity so that I can onboard new users and provide them with secure access to the system
- As a admin user, I want to see a list of keystores/identities so that I can monitor and manage all users in the system
- As a admin user, I want to update my profile so that I can maintain current administrative contact information
- As a admin user, I want to remove keystores/identities from the list so that I can revoke access for users who are no longer authorized or have left the organization
- As a admin user, I want to see details of each keystores/identities so that I can verify user information and troubleshoot access issues
- As a admin user, I want to see a list of all identities known to a node so that I can monitor network participation and identify potential security issues
- As a admin user, I want to publish an identity to a node so that I can ensure proper identity propagation across the distributed network
- As a admin user, I want to manage credentials so that I can configure and maintain secure access to external services and repositories

## Admin User - Manage Data Object Repository (DOR)
- As a admin user, I want to add a Data Object into the DOR so that I can store and share my simulation data with other users in the network
- As a admin user, I want to remove a Data Object from the DOR so that I can clean up outdated or incorrect data and manage storage space
- As a admin user, I want to grant access to a Data Object from the DOR so that I can collaborate with specific users while maintaining control over my data
- As a admin user, I want to revoke access to a Data Object from the DOR so that I can remove access for users who no longer need it or should not have access

## Admin User - Manage Processor
- As a admin user, I want to build processor image from a local source so that I can use computational models from my local development environment
- As a admin user, I want to build processor image from a Github source so that I can use computational models from version-controlled repositories

## Admin User - Job
- As a admin user, I want to submit a job so that I can execute my computational models and simulations on the distributed infrastructure
- As a admin user, I want to review results from a job so that I can analyze the output and make decisions based on the simulation results

### Admin User - Monitoring
- As a admin user, I want to monitor nodes so that I can ensure system health, performance, and security across the distributed network 