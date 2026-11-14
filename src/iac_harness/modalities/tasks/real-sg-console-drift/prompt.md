Created a Security Group in AWS using Terraform code.
No issues there and the resource creation + deletion works as expected.
If I modify anything referenced in the input code (in main.tf), TF does catch those changes in TF Plan.
But if I modify anything in AWS for same resource (in this case adding / modifying ingress rule), TF does NOT catch those changes for some reason.
