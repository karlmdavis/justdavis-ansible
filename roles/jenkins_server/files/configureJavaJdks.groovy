//
// Configure Tool - Java JDKs
//
// Configures the Java JDKs in Jenkins.
//
// References:
// * https://www.snip2code.com/Snippet/1484878/Jenkins-init-groovy-d-JDK-Installer/
// * https://stackoverflow.com/a/19855425/1851299


// These are the basic imports that Jenkin's interactive script console 
// automatically includes.
import jenkins.*;
import jenkins.model.*;
import hudson.*;
import hudson.model.*;

// Grab the Jenkins JDK config object.
jenkinsJdkConfig = Jenkins.instance.getDescriptor("hudson.model.JDK")

// Define the set of JDKs managed by the OS package manager.
systemJdks = [
  "openjdk-8-jdk": "/usr/lib/jvm/java-8-openjdk-amd64"
]

// Determine which system JDKs aren't yet registered in Jenkins.
systemJdksToRegister = []
for (systemJdk in systemJdks) {
  systemJdkAlreadyRegistered = false
  for (jenkinsJdkInstallation in jenkinsJdkConfig.installations) {
    if (systemJdk.key == jenkinsJdkInstallation.name)
      systemJdkAlreadyRegistered = true
  }

  if (!systemJdkAlreadyRegistered)
    systemJdksToRegister.add(systemJdk)
}
println("Need to register these JDKs: " + systemJdksToRegister)

// Register the system JDKs that need to be.
jenkinsJdkInstallations = []
jenkinsJdkInstallations.addAll(jenkinsJdkConfig.installations)
println("Current registered JDKs: " + jenkinsJdkInstallations)
for (systemJdkToRegister in systemJdksToRegister) {
  jenkinsJdkInstallations.push(new JDK(systemJdkToRegister.key, systemJdkToRegister.value))
}
if (!systemJdksToRegister.empty) {
  jenkinsJdkConfig.installations = jenkinsJdkInstallations.toArray(new JDK[0])
  println("Changed JDK installations: " + jenkinsJdkConfig.installations)
}
